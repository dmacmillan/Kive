import logging

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Q
from rest_framework import permissions
from rest_framework.decorators import action
from rest_framework.exceptions import APIException
from rest_framework.response import Response

from archive.serializers import RunSerializer
from kive.ajax import IsDeveloperOrGrantedReadOnly, RemovableModelViewSet,\
    CleanCreateModelMixin, convert_validation, StandardPagination,\
    SearchableModelMixin
from metadata.models import AccessControl
from pipeline.models import Pipeline, PipelineFamily
from pipeline.serializers import PipelineFamilySerializer, PipelineSerializer,\
    PipelineStepUpdateSerializer
from portal.views import developer_check

LOGGER = logging.getLogger(__name__)


class PipelineFamilyViewSet(CleanCreateModelMixin,
                            RemovableModelViewSet,
                            SearchableModelMixin):
    """ Pipeline Families that contain the different versions of each pipeline

    Query parameters for the list view:
    * is_granted=true - For administrators, this limits the list to only include
        records that the user has been explicitly granted access to. For other
        users, this has no effect.
    * filters[n][key]=x&filters[n][val]=y - Apply different filters to the
        search for pipeline families. n starts at 0 and increases by 1 for each
        added filter.
    * filters[n][key]=smart&filters[n][val]=match - pipeline families where the
        pipeline family name or description contain the value (case insensitive)
    """
    queryset = PipelineFamily.objects.all()
    serializer_class = PipelineFamilySerializer
    permission_classes = (permissions.IsAuthenticated, IsDeveloperOrGrantedReadOnly)
    pagination_class = StandardPagination

    # noinspection PyUnusedLocal
    @action(detail=True)
    def pipelines(self, request, pk=None):
        """In this routine, we are responding to an API request which looks something
        like: 'hostname:/api/pipelinefamilies/2/pipelines/'

        """
        qp = self.request.query_params
        if qp.get('is_granted') == 'true':
            is_developer = False
        else:
            is_developer = developer_check(self.request.user)
        only_is_published = qp.get('only_is_published') == 'true'
        LOGGER.debug("ISPUBLISHED {} ISADMIN {}".format(only_is_published, is_developer))

        qs = self.get_object().members.all()
        # Filter out the unpublished ones if necessary.
        if only_is_published:
            qs = qs.filter(published=True)

        member_pipelines = AccessControl.filter_by_user(request.user,
                                                        is_admin=is_developer,
                                                        queryset=qs)

        member_serializer = PipelineSerializer(member_pipelines, many=True,
                                               context={"request": request})
        return Response(member_serializer.data)

    def get_serializer_context(self):
        """
        Return the context for the serializer.

        Here, we add the only_is_published flag to the context.
        """
        context = super(PipelineFamilyViewSet, self).get_serializer_context()
        is_developer = developer_check(self.request.user)
        context["only_is_published"] = not is_developer
        return context

    def filter_queryset(self, queryset):
        queryset = super(PipelineFamilyViewSet, self).filter_queryset(queryset)
        return self.apply_filters(queryset)

    @staticmethod
    def _add_filter(queryset, key, value):
        """
        Filter the specified queryset by the specified key and value.
        """
        if key == 'smart':
            return queryset.filter(Q(name__icontains=value) |
                                   Q(description__icontains=value))
        if key == 'name':
            return queryset.filter(name__icontains=value)
        if key == 'description':
            return queryset.filter(description__icontains=value)
        if key == "user":
            return queryset.filter(user__username__icontains=value)
        raise APIException('Unknown filter key: {}'.format(key))


class PipelineViewSet(CleanCreateModelMixin,
                      RemovableModelViewSet,
                      SearchableModelMixin):
    queryset = Pipeline.objects.all()
    serializer_class = PipelineSerializer
    permission_classes = (permissions.IsAuthenticated, IsDeveloperOrGrantedReadOnly)
    pagination_class = StandardPagination

    def get_queryset(self):
        prefetchd = Pipeline.objects.prefetch_related(
            'steps__transformation__method__family',
            'steps__transformation__pipeline__family',
            'steps__transformation__inputs__structure',
            'steps__transformation__outputs__structure',
            'steps__transformation__method__family',
            'steps__cables_in__custom_wires',
            'steps__cables_in__dest__transformationinput',
            'steps__cables_in__dest__transformationoutput',
            'steps__cables_in__source__transformationinput',
            'steps__cables_in__source__transformationoutput',
            'steps__outputs_to_delete',
            'inputs__structure',
            'inputs__transformation',
            'outcables__source__structure',
            'outcables__source__transformationinput',
            'outcables__source__transformationoutput',
            'outcables__custom_wires__source_pin',
            'outcables__custom_wires__dest_pin',
            'outcables__pipeline',
            'outcables__output_cdt',
            'outputs__structure')
        # .select_related(
        #     'steps__transformation__pipeline',
        #     'steps__transformation__method',
        #     'outcables__pipeline',
        #     'outcables__output_cdt',
        #     'outcables__source'
        #     'inputs__transformation'
        # )

        return prefetchd

    # noinspection PyUnusedLocal
    @action(detail=True, suffix='Step Updates')
    def step_updates(self, request, pk=None):
        updates = self.get_object().find_step_updates()
        return Response(PipelineStepUpdateSerializer(updates,
                                                     context={'request': request},
                                                     many=True).data)

    # Override perform_create to call complete_clean, not just clean.
    @transaction.atomic
    def perform_create(self, serializer):
        """
        Handle creation and cleaning of a new object.
        """
        try:
            new_pipeline = serializer.save()
            new_pipeline.complete_clean()
        except DjangoValidationError as ex:
            raise convert_validation(ex)

    # noinspection PyUnusedLocal
    def partial_update(self, request, pk=None):
        """
        Defines PATCH functionality on a Pipeline.
        """
        if "published" in request.data:
            # This is a PATCH to publish/unpublish this Pipeline.
            return self.change_published_version(request)

        return Response({"message": "No action taken."})

    def change_published_version(self, request):
        pipeline_to_change = self.get_object()
        if request.data.get("published") == "" or request.data.get("published") is None:
            return Response({"message": "published is unspecified."})

        publish_update = request.data.get("published", "false") == "true"
        pipeline_to_change.published = publish_update
        pipeline_to_change.save()
        response_msg = 'Pipeline "{}" has been {}published.'.format(
            pipeline_to_change,
            "" if publish_update else "un"
        )
        return Response({'message': response_msg})

    def filter_queryset(self, queryset):
        queryset = super(PipelineViewSet, self).filter_queryset(queryset)
        return self.apply_filters(queryset)

    @staticmethod
    def _add_filter(queryset, key, value):
        """
        Filter the specified queryset by the specified key and value.
        """
        if key == 'smart':
            return queryset.filter(Q(revision_name__icontains=value) |
                                   Q(revision_desc__icontains=value))
        if key == 'pipelinefamily_id':
            return queryset.filter(family__id=value)
        if key == 'name':
            return queryset.filter(revision_name__icontains=value)
        if key == 'description':
            return queryset.filter(revision_desc__icontains=value)
        if key == "user":
            return queryset.filter(user__username__icontains=value)
        raise APIException('Unknown filter key: {}'.format(key))

    # noinspection PyUnusedLocal
    @action(detail=True, suffix="Instances")
    def instances(self, request, pk=None):
        queryset = self.get_object().pipeline_instances.all()
        page = self.paginate_queryset(queryset)
        if page is not None:
            # Not paging.
            serializer = RunSerializer(page,
                                       many=True,
                                       context=self.get_serializer_context())
            return self.get_paginated_response(serializer.data)
        serializer = RunSerializer(queryset,
                                   many=True,
                                   context=self.get_serializer_context())
        return Response(serializer.data)
