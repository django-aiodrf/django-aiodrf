"""Small ordinary DRF app used as the executable before/after migration fixture."""

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.routers import SimpleRouter

from tests.testapp.models import Author


class Input(serializers.ModelSerializer):
    class Meta:
        model = Author
        fields = ["id", "name"]

    def validate_name(self, value):
        return value.strip()


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

    @action(detail=False)
    def selected(self, request):
        return Response({"audit": self.get_serializer_context()["audit"]})

    @action(detail=False)
    def denied(self, request):
        raise PermissionDenied("no action")


router = SimpleRouter()
router.register("authors", Authors, basename="migration-author")
urlpatterns = router.urls
