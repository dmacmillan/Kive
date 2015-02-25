from django.template import loader, RequestContext
from django.http import HttpResponse, Http404
from django.contrib.auth.decorators import login_required

import librarian.models
import archive.models
import pipeline.models
from sandbox.forms import PipelineSelectionForm
import metadata.forms


@login_required
def choose_pipeline(request):
    """Create forms for all Pipelines in Shipyard."""
    context = RequestContext(request)
    template = loader.get_template("sandbox/choose_pipeline.html")
    families = pipeline.models.PipelineFamily.filter_by_user(request.user)
    forms = []
    for family in families:
        if len(family.complete_members) > 0:
            forms.append(PipelineSelectionForm(pipeline_family_pk=family.pk))
    context.update({"pipeline_forms": forms})
    return HttpResponse(template.render(context))


@login_required
def choose_inputs(request):
    """Load the input selection page."""
    context = RequestContext(request)
    acf = metadata.forms.AccessControlForm()

    if request.method == "GET":
        template = loader.get_template("sandbox/choose_inputs.html")
        pipeline_pk = int(request.GET.get("pipeline"))

        response_data = []
        my_pipeline = pipeline.models.Pipeline.objects.get(pk=pipeline_pk)

        # Find all compatible datasets for each input.
        for my_input in my_pipeline.inputs.order_by("dataset_idx"):
            viewable_SDs = librarian.models.SymbolicDataset.filter_by_user(request.user)
            query = archive.models.Dataset.objects.filter(symbolicdataset__in=viewable_SDs).order_by(
                "-date_created")
            if my_input.is_raw():
                query = query.filter(symbolicdataset__structure__isnull=True)
            else:
                compound_datatype = my_input.get_cdt()
                query = query.filter(
                    symbolicdataset__structure__compounddatatype=compound_datatype)
            count = query.count()
            datasets = query[:10]
            response_data.append((my_input, datasets, count))

        context.update({"input_data": response_data, "access_control_form": acf})
        return HttpResponse(template.render(context))
    else:
        # Method not allowed
        return HttpResponse(status=405)

@login_required
def active_runs(request):
    """Display all active runs for this user."""
    context = RequestContext(request)
    template = loader.get_template("sandbox/active_runs.html")
    return HttpResponse(template.render(context))


@login_required
def view_results(request, id):
    """View outputs from a pipeline run."""
    template = loader.get_template("sandbox/view_results.html")
    context = RequestContext(request)

    four_oh_four = False
    try:
        run = archive.models.Run.objects.get(pk=id)
        if not run.can_be_accessed(request.user):
            four_oh_four = True
    except archive.models.Run.DoesNotExist:
        four_oh_four = True

    if four_oh_four:
        raise Http404("ID {} does not exist or is not accessible".format(id))
    context.update({"run": run})
    return HttpResponse(template.render(context))
