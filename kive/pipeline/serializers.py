import logging
from rest_framework import serializers

from kive.serializers import AccessControlSerializer
from method.models import CodeResourceRevision, Method
from method.serializers import CodeResourceRevisionSerializer, MethodSerializer
from pipeline.models import PipelineFamily, Pipeline, CustomCableWire,\
    PipelineStepInputCable, PipelineStep, PipelineOutputCable
from transformation.models import XputStructure
from transformation.serializers import TransformationInputSerializer,\
    TransformationOutputSerializer
# from metadata.models import KiveUser
# from portal.views import developer_check

LOGGER = logging.getLogger('pipeline.ajax')


class CustomCableWireSerializer(serializers.ModelSerializer):
    source_idx = serializers.SerializerMethodField()
    dest_idx = serializers.SerializerMethodField()

    class Meta:
        model = CustomCableWire
        fields = ("source_pin", "dest_pin", "source_idx", "dest_idx")

    def get_source_idx(self, obj):
        if not obj:
            return None
        return obj.source_pin.column_idx

    def get_dest_idx(self, obj):
        if not obj:
            return None
        return obj.dest_pin.column_idx


class PipelineStepInputCableSerializer(serializers.ModelSerializer):

    # FIXME when deserializing, is_valid() puts source_dataset_name and dest_dataset_name into
    # a place that is sensible but doesn't appear in the documentation.
    # If the resulting dictionary is d, they appear in
    # d["source"|"dest"]["definite"]["dataset_name"].
    # We'll go with it for now but this may need to be fixed if they change it in the future.
    source_dataset_name = serializers.CharField(source="source.definite.dataset_name", required=True)
    dest_dataset_name = serializers.CharField(source="dest.definite.dataset_name", required=True)
    custom_wires = CustomCableWireSerializer(many=True, allow_null=True, required=False)

    class Meta:
        model = PipelineStepInputCable
        fields = (
            "source_step",
            "source",
            "source_dataset_name",
            "dest",
            "dest_dataset_name",
            "custom_wires",
            "keep_output"
        )
        read_only_fields = ("source", "dest")


class PipelineStepSerializer(serializers.ModelSerializer):
    cables_in = PipelineStepInputCableSerializer(many=True)
    transformation_family = serializers.IntegerField(
        source="transformation.definite.family.pk",
        read_only=True
    )
    inputs = TransformationInputSerializer(many=True, read_only=True)
    outputs = TransformationOutputSerializer(many=True, read_only=True)
    new_code_resource_revision_id = serializers.IntegerField(write_only=True,
                                                             required=False,
                                                             allow_null=True)
    new_dependency_ids = serializers.ListField(child=serializers.IntegerField(),
                                               write_only=True,
                                               required=False)
    outputs_to_delete = serializers.SlugRelatedField(
        many=True,
        read_only=True,
        slug_field="dataset_name"
    )
    new_outputs_to_delete_names = serializers.ListField(
        child=serializers.CharField(),
        write_only=True,
        required=False
    )

    class Meta:
        model = PipelineStep
        fields = (
            "transformation",
            "transformation_family",
            "step_num",
            "outputs_to_delete",
            "x",
            "y",
            "fill_colour",
            "name",
            "cables_in",
            "outputs",
            "inputs",
            "new_code_resource_revision_id",
            "new_dependency_ids",
            "new_outputs_to_delete_names"
        )

    def validate(self, data):
        """
        Check that the cables point to actual inputs of this PipelineStep.
        """
        curr_transf = data["transformation"].definite
        for cable_data in data["cables_in"]:
            # FIXME this is a workaround for weird deserialization behaviour.
            curr_dest_name = cable_data["dest"]["definite"]["dataset_name"]
            if not curr_transf.inputs.filter(dataset_name=curr_dest_name).exists():
                raise serializers.ValidationError(
                    'Step {} has no input named "{}"'.format(data["step_num"],
                                                             curr_dest_name)
                )

        for otd_name in data.get("new_outputs_to_delete_names", []):
            if not curr_transf.outputs.filter(dataset_name=otd_name).exists():
                raise serializers.ValidationError(
                    'Step {} has no output named "{}"'.format(data["step_num"], otd_name)
                )

        return data


class PipelineStepUpdateSerializer(serializers.Serializer):
    step_num = serializers.IntegerField()
    method = MethodSerializer()
    code_resource_revision = CodeResourceRevisionSerializer()
    dependencies = serializers.ListSerializer(
        child=CodeResourceRevisionSerializer())


class PipelineOutputCableSerializer(serializers.ModelSerializer):

    source_dataset_name = serializers.CharField(source="source.dataset_name", required=True)
    custom_wires = CustomCableWireSerializer(many=True, allow_null=True, required=False)

    x = serializers.FloatField(write_only=True)
    y = serializers.FloatField(write_only=True)

    class Meta:
        model = PipelineOutputCable
        fields = (
            "pk",
            "output_idx",
            "output_name",
            "output_cdt",
            "x",
            "y",
            "source_step",
            "source",
            "source_dataset_name",
            "custom_wires"
        )
        read_only_fields = ("source",)


# This is analogous to CRRevisionNumberGetter.
class PipelineRevisionNumberGetter(object):
    """
    Handles retrieving the default revision number for a new Pipeline.

    This is completely analogous to CRRevisionNumberGetter, and if that breaks
    due to changes in the internals of DRF, this probably will too.
    """
    def set_context(self, rev_num_field):
        self.pipelinefamily = PipelineFamily.objects.get(
            name=rev_num_field.parent.initial_data.get("family")
        )

    def __call__(self):
        return self.pipelinefamily.next_revision()


def _non_pipeline_input_cable_validate_helper(step_num, dataset_name, step_data_dicts):
    """
    Helper that validates that cables are properly fed.

    PRE: each dictionary in step_data_dicts is valid in the sense of the validated_data coming
    from a PipelineStepSerializer.
    """
    found = False
    for specified_step_data in step_data_dicts:
        if specified_step_data["step_num"] == step_num:
            found = True
            break

    if not found:
        raise serializers.ValidationError(
            "Step {} does not exist".format(step_num)
        )

    # By this point we know specified_step_data["transformation"]
    # is well-defined.
    step_transf = specified_step_data["transformation"].definite
    if not step_transf.outputs.filter(dataset_name=dataset_name).exists():
        raise serializers.ValidationError(
            'Step {} has no output named "{}"'.format(step_num,
                                                      dataset_name)
        )


def _source_transf_finder(step_num, dataset_name, step_data_dicts):
    """
    Get the specified output of a PipelineStep.

    PRE: each step in steps is valid.
    """
    # This has been validated so we can be sure that the source step
    # is well-specified.
    for specified_step_data in step_data_dicts:
        if specified_step_data["step_num"] == step_num:
            break

    curr_transf = specified_step_data["transformation"].definite
    return curr_transf.outputs.get(dataset_name=dataset_name)


class PipelineSummarySerializer(AccessControlSerializer, serializers.ModelSerializer):
    inputs = TransformationInputSerializer(many=True)

    class Meta:
        model = Pipeline
        fields = ("id", "display_name", "url", "published", 'revision_number', 'inputs')


class PipelineSerializer(AccessControlSerializer,
                         serializers.ModelSerializer):

    family = serializers.SlugRelatedField(
        slug_field='name',
        queryset=PipelineFamily.objects.all()
    )
    family_url = serializers.HyperlinkedRelatedField(
        source='family',
        view_name='pipelinefamily-detail',
        lookup_field='pk',
        read_only=True)
    inputs = TransformationInputSerializer(many=True)
    outputs = TransformationOutputSerializer(many=True, read_only=True)

    # revision_number = serializers.IntegerField(read_only=True, required=False)
    steps = PipelineStepSerializer(many=True)
    outcables = PipelineOutputCableSerializer(many=True)

    removal_plan = serializers.HyperlinkedIdentityField(view_name='pipeline-removal-plan')
    step_updates = serializers.HyperlinkedIdentityField(view_name='pipeline-step-updates')

    # This is as per CodeResourceRevisionSerializer.
    revision_number = serializers.IntegerField(
        read_only=True,
        default=PipelineRevisionNumberGetter()
    )
    pipeline_instances = serializers.HyperlinkedIdentityField(view_name='pipeline-instances')

    class Meta:
        model = Pipeline
        fields = (
            'id',
            'url',
            'family',
            'family_url',
            "display_name",
            'revision_name',
            "revision_desc",
            'revision_number',
            "revision_parent",
            "revision_DateTime",
            "published",
            'user',
            "users_allowed",
            "groups_allowed",
            'inputs',
            "outputs",
            "steps",
            "outcables",
            'removal_plan',
            'step_updates',
            "absolute_url",
            "view_url",
            "pipeline_instances"
        )

    def __init__(self, *args, **kwargs):
        super(PipelineSerializer, self).__init__(*args, **kwargs)
        # Set the querysets of the related model fields.
        curr_user = self.context["request"].user
        # LOGGER.debug("PL SERIALIZER INIT %s" % self.context.get("only_is_published", False))

        revision_parent_field = self.fields["revision_parent"]
        revision_parent_field.queryset = Pipeline.filter_by_user(curr_user)

        family_field = self.fields["family"]
        family_field.queryset = PipelineFamily.filter_by_user(curr_user)

    # def to_representation(self, instance):
    #     """ We override this method here in order to handle the
    #     case where a non-staff member should only see published pipeline versions.
    #
    #     This routine normally returns an OrderedDict instance
    #     """
    #     only_is_published = self.context.get("only_is_published", False)
    #     LOGGER.debug("TOREP  %s: %s" % (only_is_published, instance.published))
    #     if only_is_published and not instance.published:
    #         return None
    #     return super(PipelineSerializer, self).to_representation(instance)

    def validate(self, data):
        """
        Check that cables in the Pipeline are properly specified.
        """
        data = super(PipelineSerializer, self).validate(data)
        input_names = [x["dataset_name"] for x in data["inputs"]]

        for step_data in data["steps"]:
            for cable_data in step_data["cables_in"]:
                curr_source_step = cable_data["source_step"]
                # FIXME this is a workaround for weird deserialization behaviour.
                curr_source_dataset_name = cable_data["source"]["definite"]["dataset_name"]

                if curr_source_step == 0:
                    if curr_source_dataset_name not in input_names:
                        raise serializers.ValidationError(
                            'Cable input with name "{}" does not exist'.format(curr_source_dataset_name)
                        )

                else:
                    _non_pipeline_input_cable_validate_helper(
                        curr_source_step, curr_source_dataset_name, data["steps"]
                    )

        for outcable_data in data["outcables"]:
            curr_source_step = outcable_data["source_step"]
            # FIXME this is a workaround for weird deserialization behaviour.
            curr_source_dataset_name = outcable_data["source"]["dataset_name"]

            if curr_source_step == 0:
                raise serializers.ValidationError(
                    "Output cable cannot be fed by a Pipeline input"
                )

            else:
                _non_pipeline_input_cable_validate_helper(curr_source_step,
                                                          curr_source_dataset_name,
                                                          data["steps"])

        return data

    def create(self, validated_data):
        """
        Create a Pipeline from deserialized and validated data.
        """
        inputs = validated_data.pop("inputs")
        steps = validated_data.pop("steps")
        outcables = validated_data.pop("outcables")

        users_allowed = validated_data.pop("users_allowed")
        groups_allowed = validated_data.pop("groups_allowed")

        # First, create the Pipeline.
        pipeline = Pipeline.objects.create(**validated_data)
        pipeline.users_allowed.add(*users_allowed)
        pipeline.groups_allowed.add(*groups_allowed)

        # Create the inputs.
        for input_data in inputs:
            structure_data = None
            if "structure" in input_data:
                structure_data = input_data.pop("structure")
            curr_input = pipeline.inputs.create(**input_data)

            if structure_data is not None:
                XputStructure(
                    transf_xput=curr_input,
                    **structure_data
                ).save()

        name_length = Method._meta.get_field('revision_name').max_length  # @UndefinedVariable
        # Next, create the PipelineSteps.
        # We keep a directory of Methods that have already been upgraded.
        methods_already_upgraded = {}
        for step_data in steps:
            cables = step_data.pop("cables_in")

            # This is a ManyToManyField so it must be populated after the step
            # itself is created.
            new_outputs_to_delete_names = step_data.pop("new_outputs_to_delete_names", [])
            new_driver_pk = step_data.pop("new_code_resource_revision_id", None)
            new_dependency_ids = step_data.pop("new_dependency_ids", [])

            # If a new Method was found by Check for Updates, then it's already
            # been put into step_data["transformation"], i.e. "old_method" is
            # already set to the new version.
            old_method = step_data["transformation"].definite
            new_method = methods_already_upgraded.get(old_method, old_method)
            if old_method not in methods_already_upgraded:
                # Look to see if we need to make a new version of the Method.
                updated_driver = old_method.driver  # default to old version
                if new_driver_pk is not None:
                    # CR has been updated
                    updated_driver = CodeResourceRevision.objects.get(pk=new_driver_pk)

                if updated_driver is None:
                    revision_name = old_method.container.tag
                    revision_description = old_method.container.description
                else:
                    revision_name = updated_driver.revision_name
                    revision_description = updated_driver.revision_desc

                resource_map = {}
                if new_dependency_ids:
                    # revised CR has new dependencies
                    dependency_revisions = CodeResourceRevision.objects.filter(id__in=new_dependency_ids)
                    revision_name += " ({})".format(
                        ', '.join([d.revision_name for d in dependency_revisions])
                    )
                    revision_description += " ({})".format(
                        '\n---\n'.join([d.revision_desc for d in dependency_revisions])
                    )
                    resource_map = {d.coderesource_id: d for d in dependency_revisions}
                if len(revision_name) > name_length:
                    revision_name = revision_name[:name_length-3] + '...'

                if new_driver_pk is not None or new_dependency_ids:
                    # Create a new Method from the updated driver and dependencies.
                    new_method = old_method.family.members.create(
                        revision_name=revision_name,
                        revision_desc=revision_description,
                        revision_parent=old_method,
                        driver=updated_driver,
                        reusable=old_method.reusable,
                        tainted=old_method.tainted,
                        threads=old_method.threads,
                        memory=old_method.memory,
                        user=pipeline.user
                    )
                    new_method.copy_io_from_parent()
                    new_method.users_allowed.add(*users_allowed)
                    new_method.groups_allowed.add(*groups_allowed)

                    for old_dependency in old_method.dependencies.select_related('requirement'):
                        dependency_revision = resource_map.get(
                            old_dependency.requirement.coderesource_id,
                            None
                        )
                        if dependency_revision is None:
                            dependency_revision = old_dependency.requirement
                        new_method.dependencies.create(
                            requirement=dependency_revision,
                            path=old_dependency.path,
                            filename=old_dependency.filename
                        )

                    # Add this to the lookup table of methods that have already seen an upgrade.
                    methods_already_upgraded[old_method] = new_method
            step_data['transformation'] = new_method

            curr_step = pipeline.steps.create(**step_data)
            curr_step.outputs_to_delete.add(
                *[step_data["transformation"].outputs.get(dataset_name=x) for x in new_outputs_to_delete_names])

            for cable_data in cables:
                custom_wires = cable_data.pop("custom_wires") if "custom_wires" in cable_data else []

                # FIXME this is a workaround for weird deserialization behaviour.
                source_dict = cable_data.pop("source")
                source_dataset_name = source_dict["definite"]["dataset_name"]
                dest_dict = cable_data.pop("dest")
                dest_dataset_name = dest_dict["definite"]["dataset_name"]
                dest = step_data["transformation"].inputs.get(
                    dataset_name=dest_dataset_name)

                source_step_num = cable_data["source_step"]
                if source_step_num == 0:
                    source = pipeline.inputs.get(dataset_name=source_dataset_name)
                else:
                    source = _source_transf_finder(source_step_num,
                                                   source_dataset_name, steps)

                curr_cable = curr_step.cables_in.create(source=source, dest=dest, **cable_data)

                for wire_data in custom_wires:
                    curr_cable.custom_wires.create(**wire_data)

        # Lastly, create the PipelineOutputCables and associated data.
        for outcable_data in outcables:
            custom_wires = outcable_data.pop("custom_wires") if "custom_wires" in outcable_data else []
            x = outcable_data.pop("x")
            y = outcable_data.pop("y")

            # FIXME this is a workaround for weird deserialization behaviour.
            source_dict = outcable_data.pop("source")
            source_dataset_name = source_dict["dataset_name"]

            source = _source_transf_finder(outcable_data["source_step"],
                                           source_dataset_name, steps)
            curr_outcable = pipeline.outcables.create(source=source, **outcable_data)
            for wire_data in custom_wires:
                curr_outcable.custom_wires.create(**wire_data)
            curr_outcable.create_output(x=x, y=y)

        return pipeline


class PipelineFamilySerializer(AccessControlSerializer,
                               serializers.ModelSerializer):
    removal_plan = serializers.HyperlinkedIdentityField(view_name='pipelinefamily-removal-plan')

    # We use a SerializerMethodField here so we can pass in the necessary context to filter
    # by the requesting user.
    members = serializers.SerializerMethodField()
    members_url = serializers.HyperlinkedIdentityField(view_name='pipelinefamily-pipelines')

    class Meta:
        model = PipelineFamily
        fields = ("id",
                  "url",
                  "name",
                  "description",
                  "user",
                  "users_allowed",
                  "groups_allowed",
                  "absolute_url",
                  "removal_plan",
                  "num_revisions",
                  "members",
                  "members_url")
        read_only_fields = ("members", )

    def get_members(self, obj):
        """
        Wrapper for a PipelineSerializer that allows us to pass in some context.
        """
        if not obj:
            return None

        only_published = self.context["only_is_published"]
        pipelines_to_show = obj.members.all()
        if only_published:
            pipelines_to_show = pipelines_to_show.filter(published=True)

        serializer = PipelineSummarySerializer(pipelines_to_show, many=True, context=self.context)
        return serializer.data
