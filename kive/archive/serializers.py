import os

from django.template.defaultfilters import filesizeformat
from django.utils import timezone
from rest_framework import serializers
from rest_framework.reverse import reverse

from archive.models import Dataset, Run, MethodOutput
from metadata.serializers import CompoundDatatypeSerializer


class TinyRunSerializer(serializers.ModelSerializer):

    class Meta:
        model = Run
        fields = ('id',)


class DatasetSerializer(serializers.ModelSerializer):

    user = serializers.StringRelatedField()
    compounddatatype = CompoundDatatypeSerializer(source='symbolicdataset.compounddatatype')
    filename = serializers.SerializerMethodField()
    filesize = serializers.IntegerField(source='get_filesize')
    filesize_display = serializers.SerializerMethodField()
    users_allowed = serializers.StringRelatedField(many=True, source="symbolicdataset.users_allowed")
    groups_allowed = serializers.StringRelatedField(many=True, source="symbolicdataset.groups_allowed")
    download_url = serializers.HyperlinkedIdentityField(view_name='dataset-download')
    removal_plan = serializers.HyperlinkedIdentityField(view_name='dataset-removal-plan')
    redaction_plan = serializers.HyperlinkedIdentityField(view_name='dataset-redaction-plan')
    symbolic_id = serializers.IntegerField(source='symbolicdataset.id')

    class Meta:
        model = Dataset
        fields = ('id',
                  'symbolic_id',
                  'url',
                  'name',
                  'description',
                  'filename',
                  'user',
                  'date_created',
                  'date_modified',
                  'download_url',
                  'compounddatatype',
                  'filesize',
                  'filesize_display',
                  'users_allowed',
                  'groups_allowed',
                  'removal_plan',
                  'redaction_plan')

    def get_filename(self, obj):
        if obj:
            return os.path.basename(obj.dataset_file.name)
    
    def get_filesize_display(self, obj):
        if obj:
            return filesizeformat(obj.get_filesize())


class MethodOutputSerializer(serializers.ModelSerializer):

    output_redaction_plan = serializers.HyperlinkedIdentityField(
        view_name='methodoutput-output-redaction-plan')
    error_redaction_plan = serializers.HyperlinkedIdentityField(
        view_name='methodoutput-error-redaction-plan')
    code_redaction_plan = serializers.HyperlinkedIdentityField(
        view_name='methodoutput-code-redaction-plan')

    class Meta:
        model = MethodOutput
        fields = ('id',
                  'url',
                  'output_redacted',
                  'error_redacted',
                  'code_redacted',
                  'output_redaction_plan',
                  'error_redaction_plan',
                  'code_redaction_plan')


class _RunDataset(object):
    def __init__(self,
                 step_name,
                 output_name,
                 type,
                 id=None,
                 size="removed",
                 date="removed",
                 url=None,
                 redaction_plan=None,
                 is_ok=True,
                 filename=None):
        self.step_name = step_name
        self.output_name = output_name
        self.type = type
        self.id = id
        self.size = size
        self.date = date
        self.url = url
        self.redaction_plan = redaction_plan
        self.is_ok = is_ok
        self.filename = filename

    def set_dataset(self, dataset, request):
        self.id = dataset.id
        self.size = dataset.dataset_file.size
        self.date = dataset.date_created
        self.url = reverse('dataset-detail',
                           kwargs={'pk': dataset.id},
                           request=request)
        self.redaction_plan = reverse('dataset-redaction-plan',
                                      kwargs={'pk': dataset.id},
                                      request=request)
        self.filename = os.path.basename(dataset.dataset_file.name)
    
    def set_redacted(self):
        self.size = self.date = 'redacted'


class RunOutputsSerializer(serializers.ModelSerializer):
    """ Serialize a run with a focus on the outputs. """
    
    output_summary = serializers.SerializerMethodField()
    input_summary = serializers.SerializerMethodField()

    class Meta:
        model = Run
        fields = ('id', 'output_summary', 'input_summary')

    def get_input_summary(self, run):
        """ Get a  list of objects that summarize all the inputs for a run.

        """

        request = self.context.get('request', None)
        inputs = []
        pipeline_inputs = run.runtoprocess.pipeline.inputs

        for i, input in enumerate(run.runtoprocess.inputs.all()):
            has_data = input.symbolicdataset.has_data()
            if has_data:
                input_name = input.symbolicdataset.dataset.name
            else:
                pipeline_input = pipeline_inputs.get(dataset_idx=input.index)
                input_name = pipeline_input.dataset_name
            input_data = _RunDataset(step_name=(i == 0 and 'Run inputs' or ''),
                                     output_name=input_name,
                                     type='dataset')
            if has_data:
                input_data.set_dataset(input.symbolicdataset.dataset, request)
            inputs += [input_data]

        for input in inputs:
            input.is_invalid = not input.is_ok and input.id is not None
            input.step_name = str(input.step_name)
            input.output_name = str(input.output_name)

            try:
                input.size += 0
                # It's a number, so format it nicely, along with date.
                input.size = filesizeformat(input.size)
                input.date = timezone.localtime(input.date).strftime(
                    '%d %b %Y %H:%M:%S')
            except TypeError:
                pass # Size was not a number, so leave it alone.

        return [inp.__dict__ for inp in inputs]


    def get_output_summary(self, run):
        """ Get a list of objects that summarize all the outputs from a run.
        
        Outputs include pipeline outputs, as well as output log, error log, and
        output cables for each step.
        """

        request = self.context.get('request', None)
        outputs = []
        for i, outcable in enumerate(run.outcables_in_order):
            if outcable.execrecord is not None:
                execrecordout = outcable.execrecord.execrecordouts.first()
                output = _RunDataset(step_name=(i == 0 and 'Run outputs' or ''),
                                output_name=outcable.pipelineoutputcable.dest,
                                type='dataset')
                if execrecordout.symbolicdataset.has_data():
                    dataset = execrecordout.symbolicdataset.dataset
                    output.set_dataset(dataset, request)
                elif execrecordout.symbolicdataset.is_redacted():
                    output.set_redacted()
    
                outputs.append(output)
            
        for runstep in run.runsteps_in_order:
            execlog = runstep.get_log()
            if execlog is None:
                continue
            methodoutput = execlog.methodoutput
    
            output = _RunDataset(step_name=runstep.pipelinestep,
                            output_name='Standard out',
                            type='stdout')
            if methodoutput.is_output_redacted():
                output.set_redacted()
                outputs.append(output)
            else:
                try:
                    output.id = methodoutput.id
                    output.size = methodoutput.output_log.size
                    output.date = execlog.end_time
                    output.url = reverse('methodoutput-detail',
                                         kwargs={'pk': methodoutput.id},
                                         request=request)
                    output.redaction_plan = reverse(
                        'methodoutput-output-redaction-plan',
                        kwargs={'pk': methodoutput.id},
                        request=request)
                    outputs.append(output)
                except ValueError:
                    pass
            output = _RunDataset(step_name="",
                            output_name='Standard error',
                            type='stderr')
            if methodoutput.is_error_redacted():
                output.set_redacted()
                outputs.append(output)
            else:
                try:
                    output.id = methodoutput.id
                    output.size = methodoutput.error_log.size
                    output.date = execlog.end_time
                    output.url = reverse('methodoutput-detail',
                                         kwargs={'pk': methodoutput.id},
                                         request=request)
                    output.redaction_plan = reverse(
                        'methodoutput-error-redaction-plan',
                        kwargs={'pk': methodoutput.id},
                        request=request)
                    outputs.append(output)
                except ValueError:
                    pass
            if runstep.execrecord is not None:
                for execrecordout in runstep.execrecord.execrecordouts_in_order:
                    output = _RunDataset(step_name='',
                                    output_name=execrecordout.generic_output,
                                    is_ok=execrecordout.is_OK(),
                                    type='dataset')
                    if execrecordout.symbolicdataset.has_data():
                        dataset = execrecordout.symbolicdataset.dataset
                        output.set_dataset(dataset, request)
                    elif execrecordout.symbolicdataset.is_redacted():
                        output.set_redacted()
        
                    outputs.append(output)
        for output in outputs:
            output.is_invalid = not output.is_ok and output.id is not None
            output.step_name = str(output.step_name)
            output.output_name = str(output.output_name)

            try:
                output.size += 0
                # It's a number, so format it nicely, along with date.
                output.size = filesizeformat(output.size)
                output.date = timezone.localtime(output.date).strftime(
                    '%d %b %Y %H:%M:%S')
            except TypeError:
                pass # Size was not a number, so leave it alone.
        
        return [output.__dict__ for output in outputs]
