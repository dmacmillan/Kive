from datetime import datetime

from django.db import transaction
from django.db.models import Q
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone

from rest_framework import permissions, status
from rest_framework.decorators import detail_route
from rest_framework.exceptions import APIException
from rest_framework.response import Response

from librarian.views import _build_download_response
from librarian.serializers import DatasetSerializer
from librarian.models import Dataset

from kive.ajax import RemovableModelViewSet, RedactModelMixin, IsGrantedReadCreate,\
    StandardPagination, CleanCreateModelMixin, SearchableModelMixin,\
    convert_validation

from librarian.models import Dataset

JSON_CONTENT_TYPE = 'application/json'


class DatasetViewSet(RemovableModelViewSet,
                     CleanCreateModelMixin,
                     RedactModelMixin,
                     SearchableModelMixin):
    """ List and modify datasets.
    
    POST to the list to upload a new dataset, DELETE an instance to remove it
    along with all runs that produced or consumed it, or PATCH is_redacted=true
    on an instance to blank its contents along with any other instances or logs
    that used it as input.
    
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
        queryset = super(DatasetViewSet, self).filter_queryset(queryset)
        return self.apply_filters(queryset)
    
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
            return queryset.filter(file_source=None)
        if key == 'cdt':
            if value == '':
                return queryset.filter(structure__isnull=True)
            else:
                return queryset.filter(structure__compounddatatype=value)

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
        return Response(DatasetSerializer(self.get_object(), context={'request': request}).data)

    @detail_route(methods=['get'])
    def download(self, request, pk=None):
        """
        Handles downloading of the Dataset.
        """
        accessible_datasets = Dataset.filter_by_user(request.user)
        dataset = self.get_object()

        if dataset not in accessible_datasets:
            return Response(None, status=status.HTTP_404_NOT_FOUND)

        return _build_download_response(dataset.dataset_file)
