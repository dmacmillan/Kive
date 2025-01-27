"""
pipeline.models

Shipyard data models relating to the (abstract) definition of
Pipeline.
"""
from __future__ import unicode_literals

from django.db import models
from django.db.models import Max
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator, validate_slug
from django.db import transaction
from django.utils.encoding import python_2_unicode_compatible
from django.core.urlresolvers import reverse

import csv
import os
import logging
import sys
import itertools

import transformation.models
import metadata.models
import librarian.models
from constants import maxlengths
from file_access_utils import confirm_file_copy, confirm_file_created, FileCreationError

logger = logging.getLogger(__name__)


class PipelineFamily(transformation.models.TransformationFamily):
    """
    PipelineFamily groups revisions of Pipelines together.

    Inherits :model:`transformation.TransformationFamily`
    Related to :model:`pipeline.Pipeline`
    """

    # Implicitly defined:
    #   members (Pipeline/ForeignKey)

    @property
    def size(self):
        """Returns size of this Pipeline's family"""
        return self.members.count()

    @property
    def absolute_url(self):
        return reverse("pipelines", kwargs={"id": self.pk})

    @property
    def num_revisions(self):
        """
        Number of revisions within this TransformationFamily
        """
        return self.size

    def max_revision(self):
        """
        Return the maximum revision number of all member Methods.
        """
        return self.members.aggregate(Max('revision_number'))['revision_number__max']

    def next_revision(self):
        """
        Return a number suitable for assigning to the next revision to be added.
        """
        max_rev = self.max_revision()
        return (max_rev if max_rev is not None else 0) + 1

    # @property
    # def published_version_display_name(self):
    #     if self.published_version is None:
    #         return None
    #     return self.published_version.display_name

    @property
    def complete_members(self):
        """Get all *complete* Pipelines in this family, in order."""
        complete_pipelines = []
        for pipeline in self.members.order_by("revision_number"):
            try:
                pipeline.complete_clean()
                complete_pipelines.append(pipeline)
            except ValidationError:
                pass
        return complete_pipelines

    @transaction.atomic
    def remove(self):
        removal_plan = self.build_removal_plan()
        metadata.models.remove_helper(removal_plan)

    @transaction.atomic
    def build_removal_plan(self):
        removal_plan = metadata.models.empty_removal_plan()
        removal_plan["PipelineFamilies"].add(self)

        for pipeline in self.members.all():
            if pipeline not in removal_plan["Pipelines"]:
                metadata.models.update_removal_plan(removal_plan, pipeline.build_removal_plan(removal_plan))

        return removal_plan


class PipelineSerializationException(Exception):
    """
    An exception class for problems arising in defining Pipelines from the UI.
    """
    def __init__(self, error_msg):
        self.error_msg = error_msg

    def __str__(self):
        return str(self.error_msg)


@python_2_unicode_compatible
class Pipeline(transformation.models.Transformation):
    """
    A particular pipeline revision.

    Inherits from :model:`transformation.models.Transformation`
    Related to :model:`pipeline.models.PipelineFamily`
    Related to :model:`pipeline.models.PipelineStep`
    Related to :model:`pipeline.models.PipelineOutputCable`
    """

    family = models.ForeignKey(PipelineFamily, related_name="members")
    revision_parent = models.ForeignKey("self", related_name="descendants", null=True, blank=True,
                                        on_delete=models.SET_NULL)

    # moved this here from Transformation so that it can be put into the
    # unique_together statement below. allowed to be blank because it's
    # automatically set on save.
    revision_number = models.PositiveIntegerField(
        'Pipeline revision number',
        help_text='Revision number of this Pipeline in its family',
        blank=True
    )

    # Is this Pipeline a published version?
    published = models.BooleanField(
        "Is this Pipeline public?",
        default=False)

    # revision_number must be unique within PipelineFamily.
    class Meta:
        unique_together = (("family", "revision_number"))
        ordering = ["family__name", "-revision_number"]

    def __str__(self):
        """Represent pipeline by revision name and pipeline family"""
        string_rep = "{}:{}".format(self.family, self.revision_number)
        if self.revision_name:
            string_rep += " ({})".format(self.revision_name)
        return string_rep

    @property
    def display_name(self):
        return '{}: {}'.format(self.revision_number, self.revision_name)

    @property
    def absolute_url(self):
        return reverse("pipeline_revise", kwargs={"id": self.pk})

    @property
    def view_url(self):
        return reverse("pipeline_view", kwargs={"id": self.pk})

    def save(self, *args, **kwargs):
        if not self.revision_number:
            self.revision_number = self.family.next_revision()
        super(Pipeline, self).save(*args, **kwargs)

    def is_method(self):
        return False

    def is_pipeline(self):
        return True

    @property
    def family_size(self):
        """Returns size of this Pipeline's family"""
        return self.family.members.count()

    @property
    def is_published_version(self):
        """Evaluate if this pipeline revision is marked as the published version"""
        return self.published

    def clean(self):
        """
        Validate pipeline revision inputs/outputs

        - Pipeline INPUTS must be consecutively numbered from 1
        - Pipeline STEPS must be consecutively starting from 1
        - Steps are clean
        - PipelineOutputCables are appropriately mapped from the pipeline's steps
        - Users/groups with access do not exceed those of the parent PipelineFamily.
        """
        # Transformation.clean() - check for consecutive numbering of
        # input/outputs for this pipeline as a whole.  This also checks for
        # coherence of permissions on the inputs/outputs, and therefore on
        # all the cables.
        super(Pipeline, self).clean()

        self.validate_restrict_access([self.family])

        # Internal PipelineSteps must be numbered consecutively from 1 to n.
        # Check that steps are clean; this also checks the cabling between steps.
        # Note: we don't call *complete_clean* because this may refer to a
        # "transient" state of the Pipeline whereby it is not complete yet.
        for i, step in enumerate(self.steps.order_by("step_num"), start=1):
            step.clean()
            if step.step_num != i:
                raise ValidationError("Steps are not consecutively numbered starting from 1")

        # Validate each PipelineOutput(Raw)Cable
        for i, outcable in enumerate(self.outcables.order_by("output_idx"), start=1):
            outcable.clean()
            if outcable.output_idx != i:
                raise ValidationError("Outputs are not consecutively numbered starting from 1")

    def complete_clean(self):
        """
        Check that the pipeline is both coherent and complete.

        Coherence is checked using clean(); the tests for completeness are:
        - there is at least 1 step
        - steps are complete, not just clean
        """
        self.clean()

        if self.steps.count() == 0:
            raise ValidationError("Pipeline {} has no steps".format(self))

        for step in self.steps.all():
            step.complete_clean()

    def create_outputs(self):
        """
        Delete existing pipeline outputs, and recreate them from output cables.

        PRE: this should only be called after the pipeline has been verified by
        clean and the outcables are known to be OK.
        """
        # Be careful if customizing delete() of TransformationOutput.
        self.outputs.all().delete()

        # outcables is derived from (PipelineOutputCable/ForeignKey).
        # For each outcable, extract the cabling parameters.
        for outcable in self.outcables.all():
            outcable.create_output()

    # Helper to create raw outcables.  This is just so that our unit tests
    # can be easily amended to work in our new scheme, and wouldn't really
    # be used elsewhere.
    @transaction.atomic
    def create_raw_outcable(self, raw_output_name, raw_output_idx,
                            source_step, source):
        """Creates a raw outcable."""
        new_outcable = self.outcables.create(
            output_name=raw_output_name,
            output_idx=raw_output_idx,
            source_step=source_step,
            source=source)
        new_outcable.full_clean()

        return new_outcable

    # Helper to create non-raw outcables with a default output_cdt equalling
    # that of the providing TO.
    @transaction.atomic
    def create_outcable(self, output_name, output_idx, source_step,
                        source, output_CDT=None):
        """Creates a non-raw outcable taking output_cdt from the providing TO."""
        output_CDT = output_CDT or source.get_cdt()
        new_outcable = self.outcables.create(
            output_name=output_name,
            output_idx=output_idx,
            source_step=source_step,
            source=source,
            output_cdt=output_CDT)

        new_outcable.full_clean()

        return new_outcable

    def is_identical(self, other):
        """Is this Pipeline identical to another one?

        Currently this is just a stub, because we don't want to
        just call transformation.is_identical (which only checks
        inputs and outputs).
        """
        raise NotImplementedError("Structural comparison not available for pipelines.")

    def check_inputs(self, inputs):
        """
        Are the supplied inputs are appropriate for this pipeline?

        We check if the input CDT's are restrictions of this pipeline's expected
        input CDT's, and that the number of rows is in the range that the pipeline
        expects. We don't rearrange inputs that are in the wrong order.
        """
        # First quick check that the number of inputs are the same.
        if len(inputs) != self.inputs.count():
            raise ValueError('Pipeline "{}" expects {} inputs, but {} were supplied'
                             .format(self, self.inputs.count(), len(inputs)))

        # Check each individual input.
        for i, supplied_input in enumerate(inputs, start=1):
            if not supplied_input.initially_OK():
                raise ValueError('Dataset {} passed as input {} to Pipeline "{}" was not initially OK'
                                 .format(supplied_input, i, self))

            pipeline_input = self.inputs.get(dataset_idx=i)
            pipeline_raw = pipeline_input.is_raw()
            supplied_raw = supplied_input.is_raw()

            if pipeline_raw != supplied_raw:
                if pipeline_raw:
                    raise ValueError('Pipeline "{}" expected input {} to be raw, but got one with CompoundDatatype '
                                     '"{}"'.format(self, i, supplied_input.get_cdt()))
                raise ValueError('Pipeline "{}" expected input {} to be of CompoundDatatype "{}", but got raw'
                                 .format(self, i, pipeline_input.get_cdt()))

            # Both are raw.
            elif pipeline_raw:
                continue

            # Neither is raw.
            supplied_cdt = supplied_input.get_cdt()
            pipeline_cdt = pipeline_input.get_cdt()

            if not supplied_cdt.is_restriction(pipeline_cdt):
                raise ValueError('Pipeline "{}" expected input {} to be of CompoundDatatype "{}", but got one with '
                                 'CompoundDatatype "{}"'.format(self, i, pipeline_cdt, supplied_cdt))

            # The CDT's match. Is the number of rows okay?
            minrows = pipeline_input.get_min_row() or 0
            maxrows = pipeline_input.get_max_row()
            # NOTE: 2018-05-30: sys.maxint changed to sys.maxsize for py3 portability below
            # under the assumption that all we need is a 'big number'
            maxrows = maxrows if maxrows is not None else sys.maxsize
            if not minrows <= supplied_input.num_rows() <= maxrows:
                raise ValueError('Pipeline "{}" expected input {} to have between {} and {} rows, but got one with {}'
                                 .format(self, i, minrows, maxrows, supplied_input.num_rows()))

    def threads_needed(self):
        if not self.steps.all():
            return 0
        return max(x.threads_needed() for x in self.steps.all())

    @transaction.atomic
    def remove(self):
        removal_plan = self.build_removal_plan()
        metadata.models.remove_helper(removal_plan)

    @transaction.atomic
    def build_removal_plan(self, removal_accumulator=None):
        removal_plan = removal_accumulator or metadata.models.empty_removal_plan()
        assert self not in removal_plan["Pipelines"]
        removal_plan["Pipelines"].add(self)

        for run in self.pipeline_instances.all():
            if run not in removal_plan["Runs"]:
                metadata.models.update_removal_plan(
                    removal_plan, run.build_removal_plan(removal_plan)
                )

        # Remove any pipeline that uses this one as a sub-pipeline.
        for ps in self.pipelinesteps.all():
            if ps.pipeline not in removal_plan["Pipelines"]:
                metadata.models.update_removal_plan(
                    removal_plan, ps.pipeline.build_removal_plan(removal_plan)
                )

        return removal_plan

    def find_step_updates(self):
        """
        Look for updated versions of all steps of this Pipeline.

        If a step's Method has been updated, that is identified as the update
        candidate.  Otherwise, we look at the driver and dependencies of
        the Method and propose a new Method that uses the new code and leaves
        all other settings the same (the path and filename of the
        dependencies are left the same, no additional dependencies are
        identified, inputs and outputs are left the same).

        TODO: update this code to handle sub-Pipelines within the Pipeline.
        """
        updates = []
        for step in self.steps.all():
            transformation = step.transformation.find_update()
            # TODO: handle nested pipelines
            if transformation is not None and transformation.is_method():
                update = PipelineStepUpdate(step.step_num)
                update.method = transformation.definite
                updates.append(update)
            else:
                step_method = step.transformation.definite
                driver = getattr(step_method, 'driver', None)
                if driver is not None:
                    next_driver = driver.find_update()
                    new_dependencies = []
                    for dependency in step_method.dependencies.all():
                        next_dependency = dependency.requirement.find_update()
                        if next_dependency is not None:
                            new_dependencies.append(next_dependency)
                    if next_driver is not None or new_dependencies:
                        update = PipelineStepUpdate(step.step_num)
                        update.code_resource_revision = next_driver
                        update.dependencies = new_dependencies
                        updates.append(update)
        return updates

    def get_all_atomic_steps_cables(self):
        """
        Returns an iterable of all atomic PipelineSteps, PSICs, and POCs that belong to this Pipeline.
        """
        # These will be lists of querysets/iterables that we will use itertools.chain to join.
        atomic_step_iterables = []
        psic_iterables = []
        poc_iterables = []

        atomic_step_pks = []
        for ps in self.steps.all():
            psic_iterables.append(ps.cables_in.all())
            if ps.is_subpipeline():
                sub_steps, sub_psics, sub_pocs = ps.child_run.get_all_atomic_steps_cables()
                atomic_step_iterables.append(sub_steps)
                psic_iterables.append(sub_psics)
                poc_iterables.append(sub_pocs)
            else:
                atomic_step_pks.append(ps.pk)
        atomic_step_iterables.append(PipelineStep.objects.filter(pk__in=atomic_step_pks))
        poc_iterables.append(self.outcables.all())

        return (itertools.chain(*atomic_step_iterables), itertools.chain(*psic_iterables),
                itertools.chain(*poc_iterables))


@python_2_unicode_compatible
class PipelineStep(models.Model):
    """
    A step within a Pipeline representing a single transformation
    operating on inputs that are either pre-loaded (Pipeline inputs)
    or derived from previous pipeline steps within the same pipeline.

    Related to :model:`archive.models.Dataset`
    Related to :model:`pipeline.models.Pipeline`
    Related to :model:`transformation.models.Transformation`
    Related to :model:`pipeline.models.PipelineStepInput`
    Related to :model:`pipeline.models.PipelineStepDelete`
    """
    pipeline = models.ForeignKey(Pipeline, related_name="steps")

    # Pipeline steps are associated with a transformation
    transformation = models.ForeignKey(transformation.models.Transformation, related_name="pipelinesteps")
    step_num = models.PositiveIntegerField(validators=[MinValueValidator(1)])

    # Which outputs of this step we want to delete.
    outputs_to_delete = models.ManyToManyField(
        "transformation.TransformationOutput",
        help_text="TransformationOutputs whose data should not be retained",
        related_name="pipeline_steps_deleting")

    # UI information.
    x = models.FloatField(default=0, validators=[MinValueValidator(0), MaxValueValidator(1)])
    y = models.FloatField(default=0, validators=[MinValueValidator(0), MaxValueValidator(1)])
    name = models.CharField(default="", max_length=maxlengths.MAX_NAME_LENGTH, blank=True)
    fill_colour = models.CharField(default="", max_length=maxlengths.MAX_COLOUR_LENGTH, blank=True)

    class Meta:
        ordering = ["step_num"]

    def __str__(self):
        """ Represent with the pipeline and step number """
        return "{}: {}".format(self.step_num, self.name)

    def is_subpipeline(self):
        """Is this PipelineStep a sub-pipeline?"""
        return self.transformation.is_pipeline()

    @property
    def inputs(self):
        """Inputs to this PipelineStep, ordered by index."""
        return self.transformation.inputs.all()

    @property
    def outputs(self):
        """Outputs from this PipelineStep, ordered by index."""
        return self.transformation.outputs.all()

    def is_cable(self):
        return False

    def recursive_pipeline_check(self, pipeline):
        """Given a pipeline, check if this step contains it.

        PRECONDITION: the transformation at this step has been appropriately
        cleaned and does not contain any circularities.  If it does this
        function can be fragile!
        """
        contains_pipeline = False

        # Base case 1: the transformation is a method and can't possibly contain the pipeline.
        if self.transformation.is_method():
            contains_pipeline = False

        # Base case 2: this step's transformation exactly equals the pipeline specified
        elif self.transformation.pipeline == pipeline:
            contains_pipeline = True

        # Recursive case: go through all of the target pipeline steps and check if
        # any substeps exactly equal the transformation: if it does, we have circular pipeline references
        else:
            transf_steps = self.transformation.definite.steps.all()
            for step in transf_steps:
                step_contains_pipeline = step.recursive_pipeline_check(pipeline)
                if step_contains_pipeline:
                    contains_pipeline = True
        return contains_pipeline

    def clean(self):
        """
        Check coherence of this step of the pipeline.

        - Does the transformation at this step contain the parent pipeline?
        - Are any inputs multiply-cabled?

        Also, validate each input cable, and each specified output deletion.

        A PipelineStep must be save()d before cables can be connected to
        it, but it should be clean before being saved. Therefore, this
        checks coherency rather than completeness, for which we call
        complete_clean() - such as cabling.
        """
        # Check the permissions on the parent Pipeline.
        self.pipeline.validate_restrict_access([self.transformation])

        # Check recursively to see if this step's transformation contains
        # the specified pipeline at all.
        self.pipeline.validate_restrict_access([self.transformation])

        if self.recursive_pipeline_check(self.pipeline):
            raise ValidationError("Step {} contains the parent pipeline".
                                  format(self.step_num))

        # Check for multiple cabling to any of the step's inputs.
        for transformation_input in self.transformation.inputs.all():
            num_matches = self.cables_in.filter(dest=transformation_input).count()
            if num_matches > 1:
                raise ValidationError(
                    "Input \"{}\" to transformation at step {} is cabled more than once".
                    format(transformation_input.dataset_name, self.step_num))

        # Validate each cable (Even though we call PS.clean(), we want complete wires)
        for curr_cable in self.cables_in.all():
            curr_cable.clean_and_completely_wired()

        # Validate each PipelineStep output deletion
        for curr_del in self.outputs_to_delete.all():
            curr_del.clean()

        # Note that outputs_to_delete takes care of multiple deletions
        # (if a TO is marked for deletion several times, it will only
        # appear once anyway).  All that remains to check is that the
        # TOs all belong to the transformation at this step.
        for otd in self.outputs_to_delete.all():
            if not self.transformation.outputs.filter(pk=otd.pk).exists():
                raise ValidationError(
                    "Transformation at step {} does not have output \"{}\"".
                    format(self.step_num, otd.definite))

    def complete_clean(self):
        """Executed after the step's wiring has been fully defined, and
        to see if all inputs are quenched exactly once.
        """
        self.clean()

        for transformation_input in self.transformation.inputs.all():
            # See if the input is specified more than 0 times (and
            # since clean() was called above, we know that therefore
            # it was specified exactly 1 time).
            num_matches = self.cables_in.filter(dest=transformation_input).count()
            if num_matches == 0:
                raise ValidationError(
                    "Input \"{}\" to transformation at step {} is not cabled".
                    format(transformation_input.dataset_name, self.step_num))

    # Helper to create *raw* cables.  This is really just so that all our
    # unit tests can be easily amended; going forwards, there's no real reason
    # to use this.
    @transaction.atomic
    def create_raw_cable(self, dest, source):
        """
        Create a raw cable feeding this PipelineStep.
        """
        new_cable = self.cables_in.create(
            dest=dest,
            source_step=0,
            source=source)
        # June 6, 2014: now that we've eliminated the GFK, we go back to using new_cable.full_clean().
        # Previously when GFKs were being used, clean_fields() was barfing.
        new_cable.full_clean()
        # new_cable.clean_fields()
        # new_cable.clean()
        # new_cable.validate_unique()
        return new_cable

    # Same for deletes.
    @transaction.atomic
    def add_deletion(self, output_to_delete):
        """
        Mark a TO for deletion.
        """
        self.outputs_to_delete.add(output_to_delete)

    def outputs_to_retain(self):
        """Returns a list of TOs this PipelineStep doesn't delete."""
        outputs_needed = []

        # Checking each TO of this PS and maintain TOs marked to be deleted
        for step_output in self.transformation.outputs.all():

            # Check if for this pipeline step we want to delete TO step_output
            if not self.outputs_to_delete.filter(pk=step_output.pk).exists():
                outputs_needed.append(step_output)

        return outputs_needed

    def threads_needed(self):
        if self.transformation.is_pipeline():
            return self.transformation.definite.threads_needed()
        return self.transformation.definite.threads


class PipelineStepUpdate(object):
    """
    A data object to hold details about how a pipeline step can be updated.
    """
    def __init__(self, step_num):
        self.step_num = step_num
        self.method = None
        self.code_resource_revision = None
        self.dependencies = []


class PipelineCable(models.Model):
    """A cable feeding into a step or out of a pipeline."""
    # Implicitly defined:
    # - custom_wires: from a FK in CustomCableWire

    def is_incable(self):
        """Is this an input cable, as opposed to an output cable?"""
        try:
            self.pipelinestepinputcable
        except PipelineStepInputCable.DoesNotExist:
            return False
        return True

    def is_outcable(self):
        """Is this an output cable, as opposed to an input cable?"""
        try:
            self.pipelineoutputcable
        except PipelineOutputCable.DoesNotExist:
            return False
        return True

    def is_cable(self):
        return True

    def is_method(self):
        return False

    def is_compatible(self, other_cable):
        """
        Cables are compatible if both are trivial, or the wiring
        matches.

        For two cables' wires to match, any wire connecting column
        indices (source_idx, dest_idx) must appear in both cables.

        PRE: self, other_cable are clean.
        """
        trivial = [self.is_trivial(), other_cable.is_trivial()]
        if all(trivial):
            return True
        elif any(trivial):
            return False

        # Both cables are non-raw and non-trivial, so check the wiring.
        for wire in self.custom_wires.all():
            corresponding_wire = other_cable.custom_wires.filter(
                    dest_pin__column_name=wire.dest_pin.column_name,
                    dest_pin__column_idx=wire.dest_pin.column_idx)

            if not corresponding_wire.exists():
                return False

            # I'm not 100% sure which direction the restrictions need to go...
            if not wire.source_pin.datatype.is_restriction(corresponding_wire.first().source_pin.datatype):
                return False
            if not corresponding_wire.first().dest_pin.datatype.is_restriction(wire.dest_pin.datatype):
                return False
        return True

    def is_trivial(self):
        """
        True if this cable is trivial; False otherwise.

        Definition of trivial:
        1) All raw cables
        2) Cables without wiring
        3) Cables with wiring that doesn't change name/idx

        PRE: cable is clean.
        """
        if self.is_raw():
            return True

        if not self.custom_wires.exists():
            return True

        for wire in self.custom_wires.all():
            if (wire.source_pin.column_idx != wire.dest_pin.column_idx or
                    wire.source_pin.column_name != wire.dest_pin.column_name):
                return False

        return True

    @property
    def definite(self):
        if self.is_incable():
            return self.pipelinestepinputcable
        else:
            return self.pipelineoutputcable

    # TODO: this needs testing
    def find_compounddatatype(self):
        """Find a CompoundDatatype for the output of this cable.

        OUTPUTS
        output_CDT  a compatible CompoundDatatype for the cable's
                    output, or None if one doesn't exist

        PRE
        this cable is neither raw nor trivial
        """
        assert not self.definite.is_raw()
        assert not self.is_trivial()
        wires = self.custom_wires.all()

        # Use wires to determine the CDT of the output of this cable
        all_members = metadata.models.CompoundDatatypeMember.objects  # shorthand
        compatible_CDTs = None
        for wire in wires:
            # Find all CompoundDatatypes with correct members.
            candidate_members = all_members.filter(datatype=wire.source_pin.datatype,
                                                   column_name=wire.dest_pin.column_name,
                                                   column_idx=wire.dest_pin.column_idx)
            candidate_CDTs = set([m.compounddatatype for m in candidate_members])
            if compatible_CDTs is None:
                compatible_CDTs = candidate_CDTs
            else:
                compatible_CDTs &= candidate_CDTs  # intersection
            if not compatible_CDTs:
                return None

        for output_CDT in compatible_CDTs:
            if output_CDT.members.count() == len(wires):
                return output_CDT

        return None

    def create_compounddatatype(self):
        """Create a CompoundDatatype for the output of this cable.

        OUTPUTS
        output_CDT  a new CompoundDatatype for the cable's output

        PRE
        this cable is neither raw nor trivial
        """

        output_CDT = metadata.models.CompoundDatatype()
        wires = self.custom_wires.all()

        # Use wires to determine the CDT of the output of this cable
        for wire in wires:
            self.logger.debug("Adding CDTM: {} {}".format(wire.dest_pin.column_name, wire.dest_pin.column_idx))
            output_CDT.members.create(datatype=wire.source_pin.datatype,
                                      column_name=wire.dest_pin.column_name,
                                      column_idx=wire.dest_pin.column_idx)

        output_CDT.clean()
        output_CDT.save()
        return output_CDT

    def run_cable(self, source, output_path, cable_record, curr_log):
        """
        Perform cable transformation on the input.
        Creates an ExecLog, associating it to cable_record.
        Source can either be a Dataset or a path to a file.

        INPUTS
        source          Either the Dataset to run through the cable, or a file path containing the data.
        output_path     where the cable should put its output
        cable_record    RSIC/ROC for this step.
        curr_log        ExecLog to fill in for execution
        """
        # Set the ExecLog's start time.
        self.logger.debug("Filling in ExecLog of record {} and running cable (source='{}', output_path='{}')"
                          .format(cable_record, source, output_path))
        curr_log.start(save=True)

        if self.is_trivial():
            self.logger.debug("Trivial cable, making link: os.link({},{})".format(source, output_path))
            source_stat = os.stat(source)

            try:
                os.link(source, output_path)
            except OSError:
                logger.warning(
                    "OSError occurred while linking %s to %s",
                    source,
                    output_path,
                    exc_info=True
                )

                # It's possible that the link actually got created, so check for that.
                if os.path.exists(output_path):
                    output_stat = os.stat(output_path)
                    if source_stat.st_ino == output_stat.st_ino:
                        logger.debug("Link was actually successful; moving on")
                    else:
                        raise

            # This isn't actually a "copy", but we perform the check anyway.
            try:
                confirm_file_copy(source, output_path)
            except FileCreationError:
                logger.warning(
                    "FileCreationError occurred while linking %s to %s",
                    source,
                    output_path,
                    exc_info=True
                )

            curr_log.stop(save=True, clean=True)
            return

        # Make a dict encapsulating the mapping required: keyed by the output column name, with value
        # being the input column name.
        source_of = {}
        column_names_by_idx = {}

        mappings = ""
        for wire in self.custom_wires.all():
            mappings += "{} wires to {}   ".format(wire.source_pin, wire.dest_pin)
            source_of[wire.dest_pin.column_name] = wire.source_pin.column_name
            column_names_by_idx[wire.dest_pin.column_idx] = wire.dest_pin.column_name

        self.logger.debug("Nontrivial cable. {}".format(mappings))

        # Construct a list with the column names in the appropriate order.
        output_fields = [column_names_by_idx[i] for i in sorted(column_names_by_idx)]

        with open(source, "rb") as infile:
            input_csv = csv.DictReader(infile)

            with open(output_path, "wb") as outfile:
                output_csv = csv.DictWriter(outfile, fieldnames=output_fields)
                output_csv.writeheader()

                for source_row in input_csv:
                    # row = {col1name: col1val, col2name: col2val, ...}
                    dest_row = {}

                    # source_of = {outcol1: sourcecol5, outcol2: sourcecol1, ...}
                    for out_col_name in source_of:
                        dest_row[out_col_name] = source_row[source_of[out_col_name]]

                    output_csv.writerow(dest_row)

        # Confirm that the file wrote correctly.
        try:
            md5 = confirm_file_created(output_path)
        except FileCreationError as e:
            self.logger.error(str(e))
            e.md5 = md5
            raise

        # Now give it the correct end_time
        curr_log.stop(save=False, clean=False)
        curr_log.complete_clean()
        curr_log.save()

        return md5

    def _wires_match(self, other_cable):
        """
        Helper used by is_restriction for both PSIC and POC.

        PRE: when this is called, we know that both cables:
         - feed the same TI if they are PSICs
         - fed by the same TO if they are POCs
        """
        # If there is non-trivial custom wiring on either, then
        # the wiring must match.
        if self.is_trivial() and other_cable.is_trivial():
            return True
        elif self.is_trivial() != other_cable.is_trivial():
            return False

        # Now we know that both have non-trivial wiring.  Check both
        # cables' wires and see if they connect corresponding pins.
        # (We already know they feed the same TransformationInput,
        # so we only have to check the indices.)
        for wire in self.custom_wires.all():
            corresp_wire = other_cable.custom_wires.get(
                dest_pin=wire.dest_pin)
            if (wire.source_pin.column_idx !=
                    corresp_wire.source_pin.column_idx):
                return False

        # Having reached this point, we know that the wiring matches.
        return True

    def _raw_clean(self):
        """
        Helper function called by clean() of both PSIC and POC on raw cables.

        PRE: cable is raw (i.e. the source and destination are both
        raw); this is enforced by clean().
        """
        # Are there any wires defined?
        if self.custom_wires.all().exists():
            raise ValidationError(
                "Cable \"{}\" is raw and should not have custom wiring defined".
                format(self))

    def clean(self):
        """This must be either a PSIC or POC."""
        if not self.is_incable() and not self.is_outcable():
            raise ValidationError("PipelineCable with pk={} is neither a PSIC nor a POC".format(self.pk))

    def find_compatible_ERs(self, input_dataset, runcable):
        """Find an ExecRecord which may be reused by this PipelineCable.

        INPUTS
        input_dataset        Dataset to feed the cable

        OUTPUTS
        execrecord      ExecRecord which may be reused, or None if no
                        ExecRecord exists
        """
        # Look at ERIs with matching input dataset.
        candidate_ERIs = librarian.models.ExecRecordIn.objects.filter(
            dataset=input_dataset).prefetch_related(
                'execrecord__execrecordins__dataset',
                'execrecord__execrecordouts__dataset',
                'execrecord__generator__methodoutput')

        candidates = []
        for candidate_ERI in candidate_ERIs:
            candidate_execrecord = candidate_ERI.execrecord
            if candidate_execrecord.is_redacted():
                continue

            candidate_component = candidate_execrecord.general_transf()

            if not candidate_component.is_cable():
                continue

            # Check that this ER is accessible by runcable.
            extra_users, extra_groups = runcable.top_level_run.extra_users_groups(
                [candidate_execrecord.generating_run])
            if len(extra_users) > 0 or len(extra_groups) > 0:
                continue

            if self.definite.is_compatible(candidate_component):
                self.logger.debug("Compatible ER found")
                candidates.append(candidate_execrecord)

        return candidates


@python_2_unicode_compatible
class PipelineStepInputCable(PipelineCable):
    """
    Represents the "cables" feeding into the transformation of a
    particular pipeline step, specifically:

    A) Destination of cable - step implicitly defined
    B) Source of the cable (source_step, source)

    Related to :model:`pipeline.models.PipelineStep`
    """
    # The step (Which has a transformation) where we define incoming cabling
    pipelinestep = models.ForeignKey(PipelineStep, related_name="cables_in")

    # Input hole (TransformationInput) of the transformation
    # at this step to which the cable leads
    dest = models.ForeignKey("transformation.TransformationInput",
                             help_text="Wiring destination input hole",
                             related_name="cables_leading_in")

    # (source_step, source) unambiguously defines
    # the source of the cable.  source_step can't refer to a PipelineStep
    # as it might also refer to the pipeline's inputs (i.e. step 0).
    source_step = models.PositiveIntegerField("Step providing the input source", help_text="Cabling source step")
    # Wiring source output hole.
    source = models.ForeignKey(transformation.models.TransformationXput)

    # Implicitly defined:
    # - custom_wires (through inheritance)

    # allow the data coming out of a PSIC to be saved.  Note that this
    # is only relevant if the PSIC is not trivial, and is false by
    # default.
    keep_output = models.BooleanField(
        "Whether or not to retain the output of this PSIC",
        help_text="Keep or delete output",
        default=False)

    # source_step must be PRIOR to this step (Time moves forward)

    # Coherence of data is already enforced by Pipeline

    def __init__(self, *args, **kwargs):
        super(PipelineStepInputCable, self).__init__(*args, **kwargs)
        self.logger = logging.getLogger(self.__class__.__name__)

    def __str__(self):
        """
        Represent PipelineStepInputCable with the pipeline step, and the cabling destination input name.

        If cable is raw, this will look like:
        [PS]:[input name](raw)
        If not:
        [PS]:[input name]
        """
        step_str = "[no pipeline step set]"
        is_raw_str = ""
        if self.pipelinestep is not None:
            step_str = str(self.pipelinestep)
        if self.is_raw():
            is_raw_str = "(raw)"
        return "{}:{}{}".format(step_str, self.dest.dataset_name, is_raw_str)

    @property
    def min_rows_out(self):
        """Minimum number of rows this cable can output."""
        return self.dest.get_min_row()

    @property
    def max_rows_out(self):
        """Maximum number of rows this cable can output."""
        return self.dest.get_max_row()

    @property
    def inputs(self):
        """Inputs to this cable (only one)."""
        return [self.source]

    @property
    def outputs(self):
        """Outputs from this cable (only one)."""
        return [self.dest]

    def clean(self):
        """
        Check coherence of the cable.

        Check in all cases:
        - Are the source and destination either both raw or both
          non-raw?
        - Does the source come from a prior step or from the Pipeline?
        - Does the cable map to an (existent) input of this step's transformation?
        - Does the requested source exist?

        If the cable is raw:
        - Are there any wires defined?  (There shouldn't be!)

        If the cable is not raw:
        - Do the source and destination 'work together' (compatible min/max)?

        Whether the input and output have compatible CDTs or have valid custom
        wiring is checked via clean_and_completely_wired.
        """
        PipelineCable.clean(self)

        if self.source.is_raw() != self.dest.is_raw():
            raise ValidationError(
                "Cable \"{}\" has mismatched source (\"{}\") and destination (\"{}\")".
                format(self, self.source, self.dest))

        # input_requested = self.source
        # requested_from = self.source_step
        # feed_to_input = self.dest
        # step_trans = self.pipelinestep.transformation

        # Does the source come from a step prior to this one?
        if self.source_step >= self.pipelinestep.step_num:
            raise ValidationError(
                "Step {} requests input from a later step".
                format(self.pipelinestep.step_num))

        # Does the specified input defined for this transformation exist?
        if not self.pipelinestep.transformation.inputs.filter(
                pk=self.dest.pk).exists():
            raise ValidationError(
                "Transformation at step {} does not have input \"{}\"".
                format(self.pipelinestep.step_num, str(self.dest)))

        # Check that the source is available.
        if self.source_step == 0:
            # Look for the desired input among the Pipeline inputs.
            pipeline_inputs = self.pipelinestep.pipeline.inputs.all()

            if self.source.definite not in pipeline_inputs:
                raise ValidationError(
                    "Pipeline does not have input \"{}\"".
                    format(str(self.source)))

        # If not from step 0, input derives from the output of a pipeline step
        else:
            # Look for the desired input among this PS' inputs.
            source_ps = self.pipelinestep.pipeline.steps.get(
                step_num=self.source_step)

            source_ps_outputs = source_ps.transformation.outputs.all()
            if self.source.definite not in source_ps_outputs:
                raise ValidationError(
                    "Transformation at step {} does not produce output \"{}\"".
                    format(self.source_step,
                           str(self.source.definite)))

        # Propagate to more specific clean functions.
        if self.is_raw():
            self._raw_clean()
        else:
            self.non_raw_clean()

    def non_raw_clean(self):
        """Helper function called by clean() to deal with non-raw cables."""
        # Check that the input and output connected by the
        # cable are compatible re: number of rows.  Don't check for
        # ValidationError because this was checked in the
        # clean() of PipelineStep.

        # These are source and destination row constraints.
        source_min_row = (0 if self.source.get_min_row() is None
                          else self.source.get_min_row())
        dest_min_row = (0 if self.dest.get_min_row() is None
                        else self.dest.get_min_row())

        # Check for contradictory min row constraints
        if (source_min_row < dest_min_row):
            raise ValidationError(
                "Data fed to input \"{}\" of step {} may have too few rows".
                format(self.dest.dataset_name, self.pipelinestep.step_num))

        # Similarly, these are max-row constraints.
        source_max_row = (float("inf") if self.source.get_max_row() is None
                          else self.source.get_max_row())
        dest_max_row = (float("inf") if self.dest.get_max_row() is None
                        else self.dest.get_max_row())

        # Check for contradictory max row constraints
        if (source_max_row > dest_max_row):
            raise ValidationError(
                "Data fed to input \"{}\" of step {} may have too many rows".
                format(self.dest.dataset_name, self.pipelinestep.step_num))

        # Validate whatever wires there already are.
        if self.custom_wires.all().exists():
            for wire in self.custom_wires.all():
                wire.clean()

    def clean_and_completely_wired(self):
        """
        Check coherence and wiring of this cable (if it is non-raw).

        This will call clean() as well as checking whether the input
        and output 'work together'.  That is, either both are raw, or
        neither are non-raw and:
         - the source CDT is a restriction of the destination CDT; or
         - there is good wiring defined.
        """
        # Check coherence of this cable otherwise.
        self.clean()

        # There are no checks to be done on wiring if this is a raw cable.
        if self.is_raw():
            return

        # If source CDT cannot feed (i.e. is not a restriction of)
        # destination CDT, check presence of custom wiring
        if not self.source.get_cdt().is_restriction(self.dest.get_cdt()):
            if not self.custom_wires.all().exists():
                raise ValidationError(
                    "Custom wiring required for cable \"{}\"".
                    format(str(self)))

        # Validate whatever wires there are.
        if self.custom_wires.all().exists():
            # Each destination CDT member of must be wired to exactly once.

            # Get the CDT members of dest.
            dest_members = self.dest.get_cdt().members.all()

            # For each CDT member, check that there is exactly 1
            # custom_wire leading to it (i.e. number of occurrences of
            # CDT member = dest_pin).
            for dest_member in dest_members:
                numwires = self.custom_wires.filter(dest_pin=dest_member).count()

                if numwires == 0:
                    raise ValidationError(
                        "Destination member \"{}\" has no wires leading to it".
                        format(str(dest_member)))

                if numwires > 1:
                    raise ValidationError(
                        "Destination member \"{}\" has multiple wires leading to it".
                        format(str(dest_member)))

    def is_raw(self):
        """True if this cable maps raw data; false otherwise."""
        return self.dest.is_raw()

    def is_restriction(self, other_cable):
        """
        Returns whether this cable is a restriction of the specified.

        More specifically, this cable is a restriction of the
        parameter if they feed the same TransformationInput and, if
        they are not raw:
         - source CDT is a restriction of parameter's source CDT
         - wiring matches

        PRE: both self and other_cable are clean.
        """
        # Trivial case.
        if self == other_cable:
            return True

        if self.dest != other_cable.dest:
            return False

        # Now we know that they feed the same TransformationInput.
        if self.is_raw():
            return True

        # From here on, we assume both cables are non-raw.
        # (They must be, since both feed the same TI and self
        # is not raw.)
        if not self.source.get_cdt().is_restriction(
                other_cable.source.get_cdt()):
            return False

        # Now we know that they feed the same TransformationInput.
        # Call _wires_match.
        return self._wires_match(other_cable)


class CustomCableWire(models.Model):
    """
    Defines a customized connection within a pipeline.

    This allows us to filter/rearrange/repeat columns when handing
    data from a source TransformationXput to a destination Xput

    The analogue here is that we have customized a cable by rearranging
    the connections between the pins.
    """
    cable = models.ForeignKey(PipelineCable, related_name="custom_wires")

    # CDT member on the source and destination output holes
    source_pin = models.ForeignKey("metadata.CompoundDatatypeMember", related_name="source_pins")
    dest_pin = models.ForeignKey("metadata.CompoundDatatypeMember", related_name="dest_pins")

    # A cable cannot have multiple wires leading to the same dest_pin
    class Meta:
        unique_together = ("cable", "dest_pin")

    def clean(self):
        """
        Check the validity of this wire.

        The wire belongs to a cable which connects a source TransformationXput
        and a destination TransformationInput:
        - wires cannot connect a raw source or a raw destination
        - source_pin must be a member of the source CDT
        - dest_pin must be a member of the destination CDT
        - source_pin datatype matches the dest_pin datatype
        """

        # You cannot add a wire if the cable is raw
        if self.cable.is_raw():
            raise ValidationError(
                "Cable \"{}\" is raw and should not have wires defined" .
                format(self.cable))

        # Wires connect either PSIC or POCs, so these cases are separate
        source_CDT_members = self.cable.source.get_cdt().members.all()  # Duck typing
        dest_CDT = None
        dest_CDT_members = None
        if self.cable.is_incable():
            dest_CDT = self.cable.dest.get_cdt()
            dest_CDT_members = dest_CDT.members.all()
        else:
            dest_CDT = self.cable.output_cdt
            dest_CDT_members = dest_CDT.members.all()

        if not source_CDT_members.filter(pk=self.source_pin.pk).exists():
            raise ValidationError(
                "Source pin \"{}\" does not come from compounddatatype \"{}\"".
                format(self.source_pin,
                       self.cable.source.get_cdt()))

        if not dest_CDT_members.filter(pk=self.dest_pin.pk).exists():
            raise ValidationError(
                "Destination pin \"{}\" does not come from compounddatatype \"{}\"".
                format(self.dest_pin,
                       dest_CDT))

        # Check that the datatypes on either side of this wire are
        # either the same, or restriction-compatible
        if not self.source_pin.datatype.is_restriction(self.dest_pin.datatype):
            raise ValidationError(
                "The datatype of the source pin \"{}\" is incompatible with the datatype of the destination pin \"{}\"".
                format(self.source_pin, self.dest_pin))

    def is_casting(self):
        """
        Tells whether the cable performs a casting on Datatypes.

        PRE: the wire must be clean (and therefore the source DT must
        at least be a restriction of the destination DT).
        """
        return self.source_pin.datatype != self.dest_pin.datatype


@python_2_unicode_compatible
class PipelineOutputCable(PipelineCable):
    """
    Defines which outputs of internal PipelineSteps are mapped to
    end-point Pipeline outputs once internal execution is complete.

    Thus, a definition of cables leading to external pipeline outputs.

    Related to :model:`pipeline.models.Pipeline`
    Related to :model:`transformation.models.TransformationOutput`
    """
    pipeline = models.ForeignKey(Pipeline, related_name="outcables")

    output_name = models.CharField(
        "Output hole name", max_length=maxlengths.MAX_NAME_LENGTH,
        help_text="Pipeline output hole name",
        validators=[validate_slug]
    )

    # We need to specify both the output name and the output index because
    # we are defining the outputs of the Pipeline indirectly through
    # this wiring information - name/index mapping is stored...?
    output_idx = models.PositiveIntegerField("Output hole index", validators=[MinValueValidator(1)],
                                             help_text="Pipeline output hole index")

    # If null, the source must be raw
    output_cdt = models.ForeignKey("metadata.CompoundDatatype", blank=True, null=True, related_name="cables_leading_to")

    # source_step refers to an actual step of the pipeline and
    # source actually refers to one of the outputs at that step.
    # This is enforced via clean()s.
    source_step = models.PositiveIntegerField("Source pipeline step number", validators=[MinValueValidator(1)],
                                              help_text="Source step at which output comes from")

    source = models.ForeignKey("transformation.TransformationOutput", help_text="Source output hole")

    # Implicitly defined through PipelineCable and a FK from CustomCableWire:
    # - custom_wires

    # Enforce uniqueness of output names and indices.
    # Note: in the pipeline, these will still need to be compared with the raw
    # output names and indices.
    class Meta:
        unique_together = (("pipeline", "output_name"),
                           ("pipeline", "output_idx"))

    def __init__(self, *args, **kwargs):
        self.logger = logging.getLogger(self.__class__.__name__)
        super(PipelineOutputCable, self).__init__(*args, **kwargs)

    def __str__(self):
        """ Represent with the pipeline name, and TO output index + name """

        pipeline_name = "[no pipeline set]"
        if self.pipeline is not None:
            pipeline_name = str(self.pipeline)

        return "POC feeding Output_idx [{}], Output_name [{}] for pipeline [{}]".format(
                self.output_idx,
                self.output_name,
                pipeline_name)

    @property
    def dest(self):
        """Where does this cable go?"""
        return self.pipeline.outputs.get(dataset_name=self.output_name)

    @property
    def min_rows_out(self):
        """Minimum number of rows this cable can output."""
        return self.source.get_min_row()

    @property
    def max_rows_out(self):
        """Maximum number of rows this cable can output."""
        return self.source.get_max_row()

    @property
    def inputs(self):
        """Inputs to this cable (only one)."""
        return [self.source]

    @property
    def outputs(self):
        """Outputs from this cable (only one)."""
        return [self.dest]

    def clean(self):
        """
        Checks coherence of this output cable.

        PipelineOutputCable must reference an existant, undeleted
        transformation output hole.  Also, if the cable is raw, there
        should be no custom wiring.  If the cable is not raw and there
        are custom wires, they should be clean.
        """
        # Step number must be valid for this pipeline
        if self.source_step > self.pipeline.steps.all().count():
            raise ValidationError(
                "Output requested from a non-existent step")

        source_ps = self.pipeline.steps.get(step_num=self.source_step)

        # Try to find a matching output hole
        if self.source.transformation.definite != source_ps.transformation.definite:
            raise ValidationError(
                "Transformation at step {} does not produce output \"{}\"".
                format(self.source_step, self.source))

        outwires = self.custom_wires.all()

        # The cable and destination must both be raw (or non-raw)
        if self.output_cdt is None and not self.is_raw():
            raise ValidationError(
                "Cable \"{}\" has a null output_cdt but its source is non-raw" .
                format(self))
        elif self.output_cdt is not None and self.is_raw():
            raise ValidationError(
                "Cable \"{}\" has a non-null output_cdt but its source is raw" .
                format(self))

        # The cable has a raw source (and output_cdt is None).
        if self.is_raw():
            self._raw_clean()

        # The cable has a nonraw source (and output_cdt is specified).
        else:
            if not self.source.get_cdt().is_restriction(
                    self.output_cdt) and not outwires.exists():
                raise ValidationError(
                    "Cable \"{}\" has a source CDT that is not a restriction of its target CDT, but no wires exist".
                    format(self))

            # Clean all wires.
            for outwire in outwires:
                outwire.clean()
                outwire.validate_unique()
                # It isn't enough that the outwires are clean: they
                # should do no casting.
                if outwire.is_casting():
                    raise ValidationError(
                        "Custom wire \"{}\" of PipelineOutputCable \"{}\" casts the Datatype of its source".
                        format(outwire, self))

    def complete_clean(self):
        """Checks completeness and coherence of this POC.

        Calls clean, and then checks that if this POC is not raw and there
        are any custom wires defined, then they must quench the output CDT.
        """
        self.clean()

        if not self.is_raw() and self.custom_wires.all().exists():
            # Check that each CDT member has a wire leading to it
            for dest_member in self.output_cdt.members.all():
                if not self.custom_wires.filter(dest_pin=dest_member).exists():
                    raise ValidationError(
                        "Destination member \"{}\" has no outwires leading to it".
                        format(dest_member))

    def is_raw(self):
        """True if this output cable is raw; False otherwise."""
        return self.source.is_raw()

    def is_restriction(self, other_outcable):
        """
        Returns whether this cable is a restriction of the specified.

        More specifically, this cable is a restriction of the
        parameter if they come from the same TransformationOutput and, if
        they are not raw:
         - destination CDT is a restriction of parameter's destination CDT
         - wiring matches

        PRE: both self and other_cable are clean.
        """
        # Trivial case.
        if self == other_outcable:
            return True

        if self.source != other_outcable.source:
            return False

        # Now we know that they are fed by the same TransformationOutput.
        if self.is_raw():
            return True

        # From here on, we assume both cables are non-raw.
        # (They must be, since both are fed by the same TO and self
        # is not raw.)
        if not self.output_cdt.is_restriction(other_outcable.output_cdt):
            return False

        # Call _wires_match.
        return self._wires_match(other_outcable)

    def create_output(self, x=0, y=0):
        """
        Creates the corresponding output for the parent Pipeline.
        """
        output_requested = self.source

        new_pipeline_output = self.pipeline.outputs.create(
            dataset_name=self.output_name,
            dataset_idx=self.output_idx,
            x=x, y=y)

        if not self.is_raw():
            # Define an XputStructure for new_pipeline_output.
            new_structure = transformation.models.XputStructure(
                transf_xput=new_pipeline_output,
                compounddatatype=self.output_cdt,
                min_row=output_requested.get_min_row(),
                max_row=output_requested.get_max_row()
            )
            new_structure.save()
