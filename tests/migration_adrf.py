"""The same migration fixture using adrf actions and serializer hooks."""

from adrf import serializers, viewsets
from adrf.routers import SimpleRouter
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission
from rest_framework.response import Response

from tests.testapp.models import Author


class Input(serializers.ModelSerializer):
    class Meta:
        model = Author
        fields = ["id", "name"]

    async def acreate(self, validated_data):
        return await Author.objects.acreate(**validated_data)


class Policy(BasePermission):
    def has_permission(self, request, view):
        return request.headers.get("X-Deny") != "1"


class AuditMixin:
    def get_serializer_context(self):
        return {**super().get_serializer_context(), "audit": True}


class Authors(AuditMixin, viewsets.ModelViewSet):
    queryset = Author.objects.order_by("pk")
    serializer_class = Input
    authentication_classes = []
    permission_classes = [Policy]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["name"]
    pagination_class = None

    async def alist(self, request, *args, **kwargs):
        return await super().alist(request, *args, **kwargs)

    @action(detail=False)
    async def selected(self, request):
        return Response({"audit": self.get_serializer_context()["audit"]})

    @action(detail=False)
    async def denied(self, request):
        raise PermissionDenied("no action")


router = SimpleRouter()
router.register("authors", Authors, basename="migration-author")
urlpatterns = router.urls
