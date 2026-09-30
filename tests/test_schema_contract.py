"""Complete OpenAPI documents validated against real request/response examples."""

import jsonschema
import pydantic
import pytest
from django.test import override_settings
from django.urls import path
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.settings import patched_settings
from drf_spectacular.validation import validate_schema
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from aiodrf import generics
from aiodrf.contrib.pydantic import PydanticSerializer
from aiodrf.response import Response
from aiodrf.test import AsyncAPIClient
from aiodrf.utils import run_sync


class Tree(pydantic.BaseModel):
    name: str = pydantic.Field(serialization_alias="label")
    children: list["Tree"] = pydantic.Field(default_factory=list)
    note: str | None = None


class TreePatch(pydantic.BaseModel):
    name: str | None = None


class TreeSerializer(PydanticSerializer):
    class Meta:
        schema = Tree
        partial_schema = TreePatch


class TreeView(generics.GenericAPIView):
    authentication_classes = []
    permission_classes = []
    serializer_class = TreeSerializer

    async def post(self, request):
        serializer = self.get_serializer(data=await request.adata())
        await serializer.ais_valid(raise_exception=True)
        return Response(await serializer.adata())

    async def patch(self, request):
        serializer = self.get_serializer(data=await request.adata(), partial=True)
        await serializer.ais_valid(raise_exception=True)
        instance = {"name": "before", **serializer.validated_data}
        return Response(await self.get_serializer(instance).adata())


urlpatterns = [
    path("tree/", TreeView.as_view()),
    path("schema/", SpectacularAPIView.as_view(), name="schema-contract"),
    path("docs/", SpectacularSwaggerView.as_view(url_name="schema-contract")),
]


@pytest.mark.parametrize(
    ("method", "payload"),
    [
        ("post", {"name": "root", "children": [{"name": "leaf", "note": None}]}),
        ("patch", {"name": "changed"}),
        ("patch", {}),
    ],
)
async def test_schema_graph_describes_live_payloads(method, payload):
    with (
        override_settings(ROOT_URLCONF=__name__),
        patched_settings({"OAS_VERSION": "3.1.0", "COMPONENT_SPLIT_REQUEST": True}),
    ):
        schema = await run_sync(SchemaGenerator().get_schema)(request=None, public=True)
        validate_schema(schema)
        operation = schema["paths"]["/tree/"][method]
        request_schema = operation["requestBody"]["content"]["application/json"][
            "schema"
        ]
        response_schema = operation["responses"]["200"]["content"]["application/json"][
            "schema"
        ]
        jsonschema.validate(
            payload, {**request_schema, "components": schema["components"]}
        )
        response = await getattr(AsyncAPIClient(), method)(
            "/tree/", payload, format="json"
        )
        assert response.status_code == 200, response.data
        jsonschema.validate(
            response.data, {**response_schema, "components": schema["components"]}
        )
        assert response.data["label"] == payload.get("name", "before")
        if method == "post":
            assert response.data["children"][0]["label"] == "leaf"
        assert (await AsyncAPIClient().get("/docs/")).status_code == 200
