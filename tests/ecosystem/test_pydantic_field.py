"""
django-pydantic-field: a model ``SchemaField`` stores a pydantic model as
JSON; its DRF ``SchemaField`` validates with a pydantic ``TypeAdapter``; the
generic ``SchemaParser[T]`` and ``SchemaRenderer[T]`` validate the whole body
and are passed as parameterized classes (``typing`` aliases, not classes);
its ``AutoSchema`` builds DRF's OpenAPI document from the pydantic schemas.
"""

import pytest
from django.test import override_settings
from django.urls import path
from django_pydantic_field.rest_framework import (
    SchemaField,
    SchemaParser,
    SchemaRenderer,
)
from django_pydantic_field.rest_framework.openapi import AutoSchema
from rest_framework import serializers
from rest_framework import views as drf_views
from rest_framework import viewsets as drf_viewsets
from rest_framework.permissions import AllowAny
from rest_framework.response import Response as DRFResponse
from rest_framework.schemas.openapi import SchemaGenerator

from aiodrf import viewsets
from aiodrf.response import Response
from aiodrf.test import count_hops
from aiodrf.views import APIView
from tests.base import both_transports
from tests.ecosystem.models import SchemaLimits, SchemaQuota

# django-pydantic-field's SchemaField is not DRF's, so its serializers cannot be
# compiled: the tests run them on DRF's code whatever fallback the run's profile sets.
pytestmark = pytest.mark.aiodrf_settings(SERIALIZER_BACKEND_FALLBACK="drf")


class QuotaSerializer(serializers.ModelSerializer):
    limits = SchemaField(schema=SchemaLimits)

    class Meta:
        model = SchemaQuota
        fields = ["id", "name", "limits"]


class Policies:
    queryset = SchemaQuota.objects.order_by("pk")
    serializer_class = QuotaSerializer
    authentication_classes = []
    permission_classes = [AllowAny]


class DRFQuotas(Policies, drf_viewsets.ModelViewSet):
    pass


class Quotas(Policies, viewsets.ModelViewSet):
    pass


class Body:
    authentication_classes = []
    permission_classes = [AllowAny]
    parser_classes = [SchemaParser[SchemaLimits]]
    renderer_classes = [SchemaRenderer[SchemaLimits]]
    schema = AutoSchema()


class DRFEcho(Body, drf_views.APIView):
    def post(self, request):
        return DRFResponse(
            request.data.model_copy(update={"burst": request.data.burst * 2})
        )


class Echo(Body, APIView):
    async def post(self, request):
        data = await request.adata()
        return Response(data.model_copy(update={"burst": data.burst * 2}))


routes = {"get": "list", "post": "create"}
urlpatterns = [
    path("drf/quotas/", DRFQuotas.as_view(routes)),
    path("aiodrf/quotas/", Quotas.as_view(routes)),
    path("drf/echo/", DRFEcho.as_view()),
    path("aiodrf/echo/", Echo.as_view()),
]
urls = override_settings(ROOT_URLCONF=__name__)


def without_id(data):
    return {key: value for key, value in data.items() if key != "id"}


@both_transports
class _PydanticFieldTests:
    @urls
    async def test_a_schema_field_round_trips(self):
        payload = {"name": "api", "limits": {"rate": 10}}
        drf = await self.api("post", "/drf/quotas/", data=payload)
        with count_hops() as hops:
            aiodrf = await self.api("post", "/aiodrf/quotas/", data=payload)
        assert aiodrf.status_code == drf.status_code == 201
        assert without_id(aiodrf.data) == without_id(drf.data)
        assert aiodrf.data["limits"] == {"rate": 10, "burst": 1}
        stored = await SchemaQuota.objects.aget(pk=aiodrf.data["id"])
        assert stored.limits == SchemaLimits(rate=10, burst=1)
        if self.transport == "asgi":
            assert hops.calls == ["CreateModelMixin._create"]

        listed = await self.api("get", "/aiodrf/quotas/")
        drf_listed = await self.api("get", "/drf/quotas/")
        assert [without_id(item) for item in listed.data] == [
            without_id(item) for item in drf_listed.data
        ]

    @urls
    async def test_pydantic_errors_are_the_same_400(self):
        for limits in ({"rate": -1}, {}, "not json", {"rate": "many"}):
            with self.subTest(limits=limits):
                payload = {"name": "api", "limits": limits}
                drf = await self.api("post", "/drf/quotas/", data=payload)
                aiodrf = await self.api("post", "/aiodrf/quotas/", data=payload)
                assert aiodrf.status_code == drf.status_code == 400
                assert aiodrf.data == drf.data
        assert await SchemaQuota.objects.acount() == 0

    @urls
    async def test_the_schema_parser_and_renderer(self):
        drf = await self.api("post", "/drf/echo/", data={"rate": 3, "burst": 2})
        with count_hops() as hops:
            aiodrf = await self.api(
                "post", "/aiodrf/echo/", data={"rate": 3, "burst": 2}
            )
        assert aiodrf.status_code == drf.status_code == 200
        assert aiodrf.content == drf.content
        assert aiodrf.json() == {"rate": 3, "burst": 4}
        if self.transport == "asgi":
            # ``SchemaParser[T]`` and ``SchemaRenderer[T]`` are typing aliases,
            # not classes, so aiodrf cannot tell that building the request
            # and negotiating are pure: both run in a thread, as does parsing.
            assert hops.calls == [
                "APIView.initialize_request",
                "APIView._negotiate",
                "Request._load_data_and_files",
            ]

        drf = await self.api("post", "/drf/echo/", data={"rate": -3})
        aiodrf = await self.api("post", "/aiodrf/echo/", data={"rate": -3})
        assert aiodrf.status_code == drf.status_code == 400
        assert aiodrf.content == drf.content


@urls
def test_the_openapi_document_is_the_same():
    def document(prefix):
        patterns = [
            pattern
            for pattern in urlpatterns
            if str(pattern.pattern).startswith(prefix)
        ]
        return SchemaGenerator(patterns=patterns).get_schema(request=None, public=True)

    def operation(schema, prefix):
        # The operation id and tag come from the class and path names.
        found = schema["paths"][f"/{prefix}/echo/"]["post"]
        return {
            key: value
            for key, value in found.items()
            if key not in ("operationId", "tags")
        }

    drf, aiodrf = document("drf/echo"), document("aiodrf/echo")
    assert operation(aiodrf, "aiodrf") == operation(drf, "drf")
    assert aiodrf["components"] == drf["components"]
    assert "SchemaLimits" in str(aiodrf["components"])
