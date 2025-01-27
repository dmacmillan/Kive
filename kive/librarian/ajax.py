import logging
from datetime import datetime

from django.db import transaction
from django.db.models import Q
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone

from rest_framework import permissions
from rest_framework.decorators import action
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.viewsets import ReadOnlyModelViewSet

from file_access_utils import build_download_response
from librarian.serializers import DatasetSerializer, ExternalFileDirectorySerializer,\
    ExternalFileDirectoryListFilesSerializer

from librarian.models import Dataset, ExternalFileDirectory

from kive.ajax import RemovableModelViewSet, RedactModelMixin, IsGrantedReadCreate,\
    StandardPagination, CleanCreateModelMixin, SearchableModelMixin,\
    convert_validation

JSON_CONTENT_TYPE = 'application/json'
logger = logging.getLogger(__name__)


class ExternalFileDirectoryViewSet(ReadOnlyModelViewSet,
                                   SearchableModelMixin):
    """
    List ExternalFileDirectories and their contents.
    """
    queryset = ExternalFileDirectory.objects.all()
    serializer_class = ExternalFileDirectorySerializer
    permission_classes = (permissions.IsAuthenticated, )
    pagination_class = StandardPagination

    list_files_serializer_class = ExternalFileDirectoryListFilesSerializer

    def filter_queryset(self, queryset):
        queryset = super(ExternalFileDirectoryViewSet, self).filter_queryset(queryset)
        return self.apply_filters(queryset)

    def _add_filter(self, queryset, key, value):
        if key == 'smart':
            return queryset.filter(Q(name__icontains=value) |
                                   Q(path__icontains=value))
        if key == 'name':
            return queryset.filter(name__icontains=value)
        if key == 'path':
            return queryset.filter(path__icontains=value)

        raise APIException('Unknown filter key: {}'.format(key))

    # noinspection PyUnusedLocal
    @action(detail=True)
    def list_files(self, request, pk=None):
        """
        Retrieves a list of choices for files in this directory.
        """
        efd = self.get_object()
        list_files_serializer = self.list_files_serializer_class(efd, context={"request": request})
        return Response(list_files_serializer.data)


class DatasetViewSet(RemovableModelViewSet,
                     CleanCreateModelMixin,
                     RedactModelMixin,
                     SearchableModelMixin):
    """ List and modify datasets.

    POST to the list to upload a new dataset, DELETE an instance to remove it
    along with all runs that produced or consumed it, or PATCH is_redacted="true"
    on an instance to blank its contents along with any other instances or logs
    that used it as input. PATCH dataset_file=null to purge a dataset's contents,
    but leave related records intact.

    Query parameters for the list view:

    * page_size=n - limit the results and page through them
    * is_granted=true - For administrators, this limits the list to only include
        records that the user has been explicitly granted access to. For other
        users, this has no effect.
    * filters[n][key]=x&filters[n][val]=y - Apply different filters to the
        search. n starts at 0 and increases by 1 for each added filter.
        Some filters just have a key and ignore the val value. The possible
        filters are listed below.
    * filters[n][key]=smart&filters[n][val]=match - name or description contain
        the value (case insensitive)
    * filters[n][key]=name&filters[n][val]=match - name contains the value (case
        insensitive)
    * filters[n][key]=description&filters[n][val]=match - description contains the value (case
        insensitive)
    * filters[n][key]=user&filters[n][val]=match - username of the creating user contains the value (case
        insensitive)
    * filters[n][key]=uploaded - only include datasets uploaded by users, not
        generated by pipeline runs.
    * filters[n][key]=user&filters[n][val]=match - username of the creating user contains the value (case
        insensitive)
    * filters[n][key]=createdafter&filters[n][val]=match - Dataset was created after this time/date
    * filters[n][key]=createdbefore&filters[n][val]=match - Dataset was created before this time/date
    * filters[n][key]=cdt&filters[n][val]=id - only include datasets with the
        compound datatype id, or raw type if id is missing.
    * filters[n][key]=md5&filters[n][val]=match - md5 checksum matches the value
    """
    queryset = Dataset.objects.all()
    serializer_class = DatasetSerializer
    permission_classes = (permissions.IsAuthenticated, IsGrantedReadCreate)
    pagination_class = StandardPagination

    def filter_granted(self, queryset):
        """ Filter a queryset to only include records explicitly granted.
        """
        return Dataset.filter_by_user(self.request.user)

    def filter_queryset(self, queryset):
        return self.apply_filters(super(DatasetViewSet, self).filter_queryset(queryset))

    def _add_filter(self, queryset, key, value):
        if key == 'smart':
            return queryset.filter(Q(name__icontains=value) |
                                   Q(description__icontains=value))
        if key == 'name':
            return queryset.filter(name__icontains=value)
        if key == 'description':
            return queryset.filter(description__icontains=value)
        if key == "user":
            return queryset.filter(user__username__icontains=value)
        if key == 'uploaded':
            return queryset.filter(is_uploaded=True)
        if key == 'cdt':
            if value == '':
                return queryset.filter(structure__isnull=True)
            else:
                return queryset.filter(structure__compounddatatype_id=int(value))
        if key == 'md5':
            return queryset.filter(MD5_checksum=value)
        if key in ('createdafter', 'createdbefore'):
            t = timezone.make_aware(datetime.strptime(value, '%d %b %Y %H:%M'),
                                    timezone.get_current_timezone())
            if key == 'createdafter':
                return queryset.filter(date_created__gte=t)
            if key == 'createdbefore':
                return queryset.filter(date_created__lte=t)
        raise APIException('Unknown filter key: {}'.format(key))

    @transaction.atomic
    def perform_create(self, serializer):
        try:
            new_dataset = serializer.save()
            new_dataset.clean()
        except DjangoValidationError as ex:
            raise convert_validation(ex)

    def patch_object(self, request, pk=None):
        obj = self.get_object()

        try:
            dataset_file = request.data["dataset_file"]
            is_purged = dataset_file is None
        except KeyError:
            # No data file in request.
            is_purged = False

        if is_purged:
            obj.dataset_file.delete(save=True)

        return Response(DatasetSerializer(obj, context={'request': request}).data)

    # noinspection PyUnusedLocal
    @action(detail=True)
    def download(self, request, pk=None):
        """
        Handles downloading of the Dataset.
        """
        dataset = self.get_object()

        return build_download_response(dataset.dataset_file)
