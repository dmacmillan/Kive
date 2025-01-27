from django.db.models import Q

from rest_framework import permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.exceptions import APIException

from file_access_utils import build_download_response
from kive.ajax import IsDeveloperOrGrantedReadOnly, RemovableModelViewSet, CleanCreateModelMixin, \
    StandardPagination, SearchableModelMixin
from method.models import CodeResourceRevision, Method, MethodFamily, CodeResource, DockerImage
from method.serializers import MethodSerializer, MethodFamilySerializer, \
    CodeResourceSerializer, CodeResourceRevisionSerializer, DockerImageSerializer
from metadata.models import AccessControl
from portal.views import admin_check


class CodeResourceViewSet(CleanCreateModelMixin,
                          RemovableModelViewSet,
                          SearchableModelMixin):
    """CodeResources define the code used in putting together Methods.

    Query parameters:

    * is_granted - true For administrators, this limits the list to only include
        records that the user has been explicitly granted access to. For other
        users, this has no effect.
    * filters[n][key]=x&filters[n][val]=y - Apply different filters to the
        search. n starts at 0 and increases by 1 for each added filter.
        Some filters just have a key and ignore the val value. The possible
        filters are listed below.
    * filters[n][key]=smart&filters[n][val]=match - name, description, or filename contains
        the value (case insensitive)
    * filters[n][key]=name&filters[n][val]=match - name contains the value (case
        insensitive)
    * filters[n][key]=description&filters[n][val]=match - description contains the value (case
        insensitive)
    * filters[n][key]=filename&filters[n][val]=match - filename contains the value (case
        insensitive)
    * filters[n][key]=user&filters[n][val]=match - username of creator contains the value (case
        insensitive)
    """
    queryset = CodeResource.objects.all()
    serializer_class = CodeResourceSerializer
    permission_classes = (permissions.IsAuthenticated, IsDeveloperOrGrantedReadOnly)
    pagination_class = StandardPagination

    @action(detail=True)
    def revisions(self, request, pk=None):
        if self.request.query_params.get('is_granted') == 'true':
            is_admin = False
        else:
            is_admin = admin_check(self.request.user)

        revisions = AccessControl.filter_by_user(
            request.user,
            is_admin=is_admin,
            queryset=CodeResourceRevision.objects.filter(coderesource_id=pk))

        return Response(
            CodeResourceRevisionSerializer(revisions, many=True, context={"request": request}).data
        )

    def filter_queryset(self, queryset):
        queryset = super(CodeResourceViewSet, self).filter_queryset(queryset)
        return self.apply_filters(queryset)

    @staticmethod
    def _add_filter(queryset, key, value):
        """
        Filter the specified queryset by the specified key and value.
        """
        if key == 'smart':
            return queryset.filter(Q(name__icontains=value) |
                                   Q(description__icontains=value) |
                                   Q(filename__icontains=value))
        if key == 'name':
            return queryset.filter(name__icontains=value)
        if key == 'filename':
            return queryset.filter(filename__icontains=value)
        if key == 'description':
            return queryset.filter(description__icontains=value)
        if key == "user":
            return queryset.filter(user__username__icontains=value)

        raise APIException('Unknown filter key: {}'.format(key))


class CodeResourceRevisionViewSet(CleanCreateModelMixin, RemovableModelViewSet,
                                  SearchableModelMixin):
    """CodeResourceRevisions are the individual revisions of CodeResources.

    Query parameters:

    * is_granted - true For administrators, this limits the list to only include
        records that the user has been explicitly granted access to. For other
        users, this has no effect.
    * filters[n][key]=x&filters[n][val]=y - Apply different filters to the
        search. n starts at 0 and increases by 1 for each added filter.
        Some filters just have a key and ignore the val value. The possible
        filters are listed below.
    * filters[n][key]=coderesource_id&filters[n][val]=match - parent CodeResource's PK equals
        the value
    * filters[n][key]=smart&filters[n][val]=match - revision name or description contains the value (case
        insensitive)
    * filters[n][key]=name&filters[n][val]=match - revision name contains the value (case
        insensitive)
    * filters[n][key]=description&filters[n][val]=match - revision description contains the value (case
        insensitive)
    * filters[n][key]=user&filters[n][val]=match - username of creator contains the value (case
        insensitive)
    """
    queryset = CodeResourceRevision.objects.all()
    serializer_class = CodeResourceRevisionSerializer
    permission_classes = (permissions.IsAuthenticated, IsDeveloperOrGrantedReadOnly)
    pagination_class = StandardPagination

    @action(detail=True)
    def download(self, request, pk=None):
        """
        Download the file pointed to by this CodeResourceRevision.
        """
        accessible_revisions = CodeResourceRevision.filter_by_user(request.user)
        if not accessible_revisions.filter(pk=pk).exists():
            return Response(None, status=status.HTTP_404_NOT_FOUND)

        revision = self.get_object()
        if not revision.content_file:
            return Response({"errors": "This CodeResourceRevision has no content file."},
                            status=status.HTTP_403_FORBIDDEN)

        return build_download_response(revision.content_file)

    def filter_queryset(self, queryset):
        queryset = super(CodeResourceRevisionViewSet, self).filter_queryset(queryset)
        return self.apply_filters(queryset)

    @staticmethod
    def _add_filter(queryset, key, value):
        """
        Filter the specified queryset by the specified key and value.
        """
        if key == 'smart':
            return queryset.filter(Q(revision_name__icontains=value) |
                                   Q(revision_desc__icontains=value))
        if key == 'coderesource_id':
            return queryset.filter(coderesource__id=value)
        if key == 'name':
            return queryset.filter(revision_name__icontains=value)
        if key == 'description':
            return queryset.filter(revision_desc__icontains=value)
        if key == "user":
            return queryset.filter(user__username__icontains=value)

        raise APIException('Unknown filter key: {}'.format(key))


class MethodFamilyViewSet(CleanCreateModelMixin,
                          RemovableModelViewSet,
                          SearchableModelMixin):
    """MethodFamilies are collections of Methods grouped by function.

    Query parameters:

    * is_granted - true For administrators, this limits the list to only include
        records that the user has been explicitly granted access to. For other
        users, this has no effect.
    * filters[n][key]=x&filters[n][val]=y - Apply different filters to the
        search. n starts at 0 and increases by 1 for each added filter.
        Some filters just have a key and ignore the val value. The possible
        filters are listed below.
    * filters[n][key]=smart&filters[n][val]=match - name, description, or filename contains
        the value (case insensitive)
    * filters[n][key]=name&filters[n][val]=match - name contains the value (case
        insensitive)
    * filters[n][key]=description&filters[n][val]=match - description contains the value (case
        insensitive)
    * filters[n][key]=user&filters[n][val]=match - username of creator contains the value (case
        insensitive)
    * filters[n][key]=code&filters[n][val]=match - name of driver or dependency
        contains the value (case insensitive)
    """
    queryset = MethodFamily.objects.all()
    serializer_class = MethodFamilySerializer
    permission_classes = (permissions.IsAuthenticated, IsDeveloperOrGrantedReadOnly)
    pagination_class = StandardPagination

    @action(detail=True)
    def methods(self, request, pk=None):
        if self.request.query_params.get('is_granted') == 'true':
            is_admin = False
        else:
            is_admin = admin_check(self.request.user)

        member_methods = AccessControl.filter_by_user(
            request.user,
            is_admin=is_admin,
            queryset=Method.objects.filter(family_id=pk))

        member_serializer = MethodSerializer(
            member_methods, many=True, context={"request": request})
        return Response(member_serializer.data)

    def filter_queryset(self, queryset):
        queryset = super(MethodFamilyViewSet, self).filter_queryset(queryset)
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
        if key == "code":
            all_matches = queryset.filter(
                Q(members__driver__coderesource__name__icontains=value) |
                Q(members__dependencies__requirement__coderesource__name__icontains=value))
            return queryset.filter(pk__in=all_matches)

        raise APIException('Unknown filter key: {}'.format(key))


class MethodViewSet(CleanCreateModelMixin, RemovableModelViewSet,
                    SearchableModelMixin):
    queryset = Method.objects.all()
    serializer_class = MethodSerializer
    permission_classes = (permissions.IsAuthenticated, IsDeveloperOrGrantedReadOnly)
    pagination_class = StandardPagination

    def filter_queryset(self, queryset):
        queryset = super(MethodViewSet, self).filter_queryset(queryset)
        return self.apply_filters(queryset)

    @staticmethod
    def _add_filter(queryset, key, value):
        """
        Filter the specified queryset by the specified key and value.
        """
        if key == 'smart':
            return queryset.filter(Q(revision_name__icontains=value) |
                                   Q(revision_desc__icontains=value))
        if key == 'methodfamily_id':
            return queryset.filter(family__id=value)
        if key == 'name':
            return queryset.filter(revision_name__icontains=value)
        if key == 'description':
            return queryset.filter(revision_desc__icontains=value)
        if key == "user":
            return queryset.filter(user__username__icontains=value)

        raise APIException('Unknown filter key: {}'.format(key))


class DockerImageViewSet(CleanCreateModelMixin,
                         RemovableModelViewSet,
                         SearchableModelMixin):
    """Docker images are isolated file systems that can run one or more methods.

    Query parameters:

    * is_granted - true For administrators, this limits the list to only include
        records that the user has been explicitly granted access to. For other
        users, this has no effect.
    * filters[n][key]=x&filters[n][val]=y - Apply different filters to the
        search. n starts at 0 and increases by 1 for each added filter.
        Some filters just have a key and ignore the val value. The possible
        filters are listed below.
    * filters[n][key]=smart&filters[n][val]=match - name, tag, git, or
        description contains the value (case insensitive)
    * filters[n][key]=name&filters[n][val]=match - name contains the value (case
        insensitive)
    * filters[n][key]=tag&filters[n][val]=match - tag contains the value (case
        insensitive)
    * filters[n][key]=git&filters[n][val]=match - git contains the value (case
        insensitive)
    * filters[n][key]=description&filters[n][val]=match - description contains
        the value (case insensitive)
    * filters[n][key]=user&filters[n][val]=match - username of creator contains the value (case
        insensitive)
    """
    queryset = DockerImage.objects.all()
    serializer_class = DockerImageSerializer
    permission_classes = (permissions.IsAuthenticated, IsDeveloperOrGrantedReadOnly)
    pagination_class = StandardPagination

    def filter_queryset(self, queryset):
        queryset = super(DockerImageViewSet, self).filter_queryset(queryset)
        return self.apply_filters(queryset)

    @staticmethod
    def _add_filter(queryset, key, value):
        """
        Filter the specified queryset by the specified key and value.
        """
        if key == 'smart':
            return queryset.filter(Q(name__icontains=value) |
                                   Q(tag__icontains=value) |
                                   Q(git__icontains=value) |
                                   Q(description__icontains=value))
        if key == 'name':
            return queryset.filter(name__icontains=value)
        if key == 'tag':
            return queryset.filter(tag__icontains=value)
        if key == 'git':
            return queryset.filter(git__icontains=value)
        if key == 'description':
            return queryset.filter(description__icontains=value)
        if key == "user":
            return queryset.filter(user__username__icontains=value)

        raise APIException('Unknown filter key: {}'.format(key))
