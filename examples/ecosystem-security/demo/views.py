"""Filter and permission integrations without replacing vendor implementations."""

from allauth.headless.contrib.rest_framework.authentication import (
    XSessionTokenAuthentication,
)
from auditlog.context import set_actor
from dj_rest_auth.jwt_auth import JWTCookieAuthentication
from django.db import transaction
from django_filters import rest_framework as filters
from guardian.shortcuts import assign_perm
from oauth2_provider.contrib.rest_framework import OAuth2Authentication, TokenHasScope
from rest_framework import serializers
from rest_framework.authentication import TokenAuthentication
from rest_framework.permissions import DjangoObjectPermissions
from rest_framework_guardian.filters import ObjectPermissionsFilter
from rules.contrib.rest_framework import AutoPermissionViewSetMixin

from aiodrf.response import Response
from aiodrf.views import APIView
from aiodrf.viewsets import ModelViewSet

from .models import Article, Note


class ReadPermissions(DjangoObjectPermissions):
    perms_map = {
        **DjangoObjectPermissions.perms_map,
        "GET": ["%(app_label)s.view_%(model_name)s"],
    }


class ArticleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Article
        fields = ["id", "title", "sensitive_note"]
        extra_kwargs = {"sensitive_note": {"write_only": True}}


class ArticleFilter(filters.FilterSet):
    title = filters.CharFilter(lookup_expr="icontains")

    class Meta:
        model = Article
        fields = ["title"]


class Articles(ModelViewSet):
    queryset = Article.objects.all()
    serializer_class = ArticleSerializer
    permission_classes = [ReadPermissions]
    filter_backends = [filters.DjangoFilterBackend, ObjectPermissionsFilter]
    filterset_class = ArticleFilter

    def perform_create(self, serializer):
        # Authentication has completed here, including token-based users.
        with set_actor(self.request.user), transaction.atomic():
            article = serializer.save(owner=self.request.user)
            for permission in ("view_article", "change_article", "delete_article"):
                assign_perm(permission, self.request.user, article)

    def perform_update(self, serializer):
        with set_actor(self.request.user), transaction.atomic():
            serializer.save()

    def perform_destroy(self, instance):
        with set_actor(self.request.user), transaction.atomic():
            instance.delete()


class NoteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Note
        fields = ["id", "text"]


class Notes(AutoPermissionViewSetMixin, ModelViewSet):
    queryset = Note.objects.none()
    serializer_class = NoteSerializer

    def get_queryset(self):
        # Object predicates do not automatically filter collection responses.
        return Note.objects.filter(owner=self.request.user).order_by("pk")

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)


class Identity(APIView):
    async def get(self, request):
        return Response({"username": request.user.get_username()})


class OAuthIdentity(Identity):
    authentication_classes = [OAuth2Authentication]
    permission_classes = [TokenHasScope]
    required_scopes = ["read"]


class AllauthIdentity(Identity):
    authentication_classes = [XSessionTokenAuthentication]


class CookieIdentity(Identity):
    authentication_classes = [JWTCookieAuthentication]


class TokenIdentity(Identity):
    authentication_classes = [TokenAuthentication]
