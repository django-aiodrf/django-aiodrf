"""Stream documentation describes items without executing or buffering a stream."""

import json
from contextlib import asynccontextmanager
from io import StringIO
from types import ModuleType
from typing import Annotated

import jsonschema
import msgspec
import pydantic
import pytest
from django.contrib.auth.models import User
from django.core.asgi import get_asgi_application
from django.core.management import call_command
from django.test import override_settings
from django.urls import path
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.settings import patched_settings
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from drf_spectacular.validation import validate_schema
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated

from aiodrf import aio
from aiodrf.contrib.msgspec import MsgspecSerializer
from aiodrf.contrib.pydantic import PydanticSerializer
from aiodrf.contrib.spectacular import StreamSchema
from aiodrf.contrib.typed import adapt
from aiodrf.response import EventStreamResponse, ServerSentEvent, StreamingResponse
from aiodrf.routers import SimpleRouter
from aiodrf.test import AsyncAPIClient
from aiodrf.utils import run_sync
from aiodrf.views import APIView
from aiodrf.viewsets import GenericViewSet
from tests.asgi_driver import ASGIDriver, http_scope


class StreamChildSerializer(serializers.Serializer):
    label = serializers.CharField(source="name", min_length=1)


class StreamItemSerializer(serializers.Serializer):
    value = serializers.IntegerField(min_value=1)
    child = StreamChildSerializer()
    secret = serializers.CharField(write_only=True, required=False)


class StreamChildStruct(msgspec.Struct):
    name: Annotated[str, msgspec.Meta(min_length=1)] = msgspec.field(name="label")


class StreamItemStruct(msgspec.Struct):
    value: Annotated[int, msgspec.Meta(ge=1)]
    child: StreamChildStruct


class StreamStructSerializer(MsgspecSerializer):
    class Meta:
        schema = StreamItemStruct


class StreamChildModel(pydantic.BaseModel):
    name: str = pydantic.Field(min_length=1, serialization_alias="label")


class StreamItemModel(pydantic.BaseModel):
    value: int = pydantic.Field(ge=1)
    child: StreamChildModel


class StreamModelSerializer(PydanticSerializer):
    class Meta:
        schema = StreamItemModel


@pytest.fixture(params=["drf", "msgspec", "pydantic"])
def item_contract(request):
    if request.param == "drf":
        return (
            StreamItemSerializer,
            {"value": 1, "child": {"label": "sample"}, "secret": "private"},
            {"value": 1, "child": {"name": "sample"}},
        )
    if request.param == "msgspec":
        return (
            StreamStructSerializer,
            {"value": 1, "child": {"label": "sample"}},
            StreamItemStruct(value=1, child=StreamChildStruct(name="sample")),
        )
    return (
        StreamModelSerializer,
        {"value": 1, "child": {"name": "sample"}},
        StreamItemModel(value=1, child=StreamChildModel(name="sample")),
    )


def resolve(document, schema):
    if "$ref" in schema:
        return document["components"]["schemas"][schema["$ref"].rsplit("/", 1)[-1]]
    return schema


def generate_schema():
    output = StringIO()
    call_command(
        "spectacular",
        "--format",
        "openapi-json",
        "--validate",
        "--fail-on-warn",
        stdout=output,
    )
    return json.loads(output.getvalue())


def validate_items(document, item_schema, values):
    schema = {**item_schema, "components": document["components"]}
    for value in values:
        jsonschema.validate(value, schema)
    for invalid in (
        {"value": 0, "child": {"label": "sample"}},
        {"value": 1, "child": {"label": ""}},
    ):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(invalid, schema)


def validate_request_shape(document, operation, payload, serializer_class):
    request_schema = operation["requestBody"]["content"]["application/json"]["schema"]
    jsonschema.validate(
        payload, {**request_schema, "components": document["components"]}
    )
    request_item = resolve(document, request_schema)
    if serializer_class is StreamItemSerializer:
        assert "secret" in request_item["properties"]
    if serializer_class is StreamModelSerializer:
        request_child = resolve(document, request_item["properties"]["child"])
        assert "name" in request_child["properties"]
        assert "label" not in request_child["properties"]


@pytest.mark.parametrize("version", ["3.0.3", "3.1.0"])
@pytest.mark.parametrize("media_type", ["application/x-ndjson", "text/event-stream"])
async def test_schema_matches_live_items_without_opening_lifespan(
    item_contract, version, media_type
):
    serializer_class, payload, instance = item_contract
    events = []

    @asynccontextmanager
    async def lifespan():
        events.append("lifespan")
        yield

    class View(APIView):
        authentication_classes = []
        permission_classes = []

        @extend_schema(
            request=serializer_class,
            responses={
                (202, media_type): OpenApiResponse(
                    StreamSchema(serializer_class), description="Accepted item stream."
                ),
                (400, "application/json"): StreamChildSerializer,
            },
        )
        async def post(self, request):
            serializer = serializer_class(data=await request.adata())
            await aio.is_valid(serializer, raise_exception=True)

            async def items():
                events.append("iterate")
                try:
                    for index in range(2):
                        item = await aio.data(serializer_class(instance))
                        if media_type == "text/event-stream":
                            yield ServerSentEvent(
                                item, id=str(index), event="item", retry=1000
                            )
                        else:
                            yield item
                finally:
                    events.append("close")

            response_class = (
                EventStreamResponse
                if media_type == "text/event-stream"
                else StreamingResponse
            )
            return response_class(items(), status=202)

    urls = ModuleType("stream_schema_urls")
    urls.urlpatterns = [path("items/", View.as_view())]
    with (
        override_settings(
            ROOT_URLCONF=urls,
            MIDDLEWARE=[],
            AIODRF={"LIFESPAN": lifespan},
        ),
        patched_settings({"OAS_VERSION": version, "COMPONENT_SPLIT_REQUEST": True}),
    ):
        document = await run_sync(generate_schema)()
        assert events == []
        operation = document["paths"]["/items/"]["post"]
        response = operation["responses"]["202"]
        assert response["description"] == "Accepted item stream."
        assert set(response["content"]) == {media_type}
        stream = resolve(document, response["content"][media_type]["schema"])
        assert stream["type"] == "string"
        item_schema = stream["x-aiodrf-item-schema"]
        error = operation["responses"]["400"]["content"]["application/json"]["schema"]
        assert resolve(document, error)["type"] == "object"
        validate_request_shape(document, operation, payload, serializer_class)
        response_item = resolve(document, item_schema)
        assert "secret" not in response_item["properties"]

        scope = http_scope("/items/", method="POST")
        body = json.dumps(payload).encode()
        scope["headers"].extend(
            [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ]
        )
        async with ASGIDriver(get_asgi_application(), scope) as driver:
            await driver.incoming.put({"type": "http.request", "body": body})
            await driver.finish()
        assert driver.sent[0]["status"] == 202, driver.sent
        assert events == ["iterate", "close"]
        headers = {
            key.lower(): value.decode() for key, value in driver.sent[0]["headers"]
        }
        assert headers[b"content-type"].startswith(media_type)
        body = b"".join(
            message.get("body", b"") for message in driver.sent[1:]
        ).decode()
        lines = body.splitlines()
        if media_type == "text/event-stream":
            assert "event: item" in lines
            assert "id: 0" in lines
            lines = [
                line.removeprefix("data: ")
                for line in lines
                if line.startswith("data: ")
            ]
        values = [json.loads(line) for line in lines]
        assert values == [{"value": 1, "child": {"label": "sample"}}] * 2
        validate_items(document, item_schema, values)
        # A fresh generator must not retain a previous registry or consume items.
        assert await run_sync(generate_schema)() == document
        assert events == ["iterate", "close"]


def test_list_action_does_not_wrap_or_paginate_stream_and_reuses_item_components(
    capsys,
):
    item = StreamItemSerializer(context={"application": "schema"})

    class View(GenericViewSet):
        authentication_classes = []
        permission_classes = []
        serializer_class = StreamItemSerializer
        pagination_class = PageNumberPagination

        @extend_schema(responses={(200, "application/x-ndjson"): StreamSchema(item)})
        async def list(self, request):
            raise AssertionError("Schema generation must not call the view.")

        @extend_schema(
            responses={(200, "text/event-stream"): StreamSchema(StreamItemSerializer)}
        )
        @action(detail=False)
        async def events(self, request):
            raise AssertionError("Schema generation must not call the action.")

        @extend_schema(
            responses={
                (200, "application/x-ndjson"): StreamSchema(StreamChildSerializer)
            }
        )
        @action(detail=False)
        async def children(self, request):
            raise AssertionError("Schema generation must not call the action.")

        @extend_schema(parameters=[OpenApiParameter("id", int, OpenApiParameter.PATH)])
        async def retrieve(self, request, pk):
            raise AssertionError("Schema generation must not call the view.")

    router = SimpleRouter()
    router.register("items", View, basename="items")
    document = SchemaGenerator(patterns=router.urls).get_schema(
        request=None, public=True
    )
    validate_schema(document)
    components = document["components"]["schemas"]
    ndjson = document["paths"]["/items/"]["get"]["responses"]["200"]["content"]
    sse = document["paths"]["/items/events/"]["get"]["responses"]["200"]["content"]
    assert (
        ndjson["application/x-ndjson"]["schema"] == sse["text/event-stream"]["schema"]
    )
    assert components["StreamItemStream"]["type"] == "string"
    assert components["StreamChildStream"]["x-aiodrf-item-schema"] == {
        "$ref": "#/components/schemas/StreamChild"
    }
    assert not any(name.startswith("Paginated") for name in components)
    ordinary = document["paths"]["/items/{id}/"]["get"]["responses"]["200"]["content"]
    assert (
        ordinary["application/json"]["schema"]
        == components["StreamItemStream"]["x-aiodrf-item-schema"]
    )
    assert item.context == {"application": "schema"}
    assert "Warning" not in capsys.readouterr().err


def test_empty_item_and_wire_examples():
    class EmptySerializer(serializers.Serializer):
        pass

    class View(APIView):
        @extend_schema(
            responses={
                (200, "text/event-stream"): OpenApiResponse(
                    StreamSchema(EmptySerializer),
                    examples=[OpenApiExample("Empty event", value="data: {}\n\n")],
                )
            }
        )
        async def get(self, request):
            raise AssertionError("Schema generation must not call the view.")

    document = SchemaGenerator(patterns=[path("events/", View.as_view())]).get_schema(
        public=True
    )
    validate_schema(document)
    media = document["paths"]["/events/"]["get"]["responses"]["200"]["content"][
        "text/event-stream"
    ]
    stream = resolve(document, media["schema"])
    assert stream["x-aiodrf-item-schema"] == {
        "type": "object",
        "additionalProperties": False,
    }
    assert media["examples"]["EmptyEvent"]["value"] == "data: {}\n\n"


@pytest.mark.parametrize("version", ["3.0.3", "3.1.0"])
def test_recursive_adapted_items_keep_real_component_definitions(version):
    class RecursiveEvent(pydantic.BaseModel):
        name: str = pydantic.Field(serialization_alias="label")
        children: list["RecursiveEvent"] = pydantic.Field(default_factory=list)

    class View(APIView):
        @extend_schema(
            responses={
                (200, "text/event-stream"): StreamSchema(adapt(RecursiveEvent)),
            }
        )
        async def get(self, request):
            raise AssertionError("Schema generation must not call the view.")

    with patched_settings({"OAS_VERSION": version}):
        document = SchemaGenerator(patterns=[path("", View.as_view())]).get_schema(
            public=True
        )
    validate_schema(document)
    stream = document["components"]["schemas"]["RecursiveEventStream"]
    item = resolve(document, stream["x-aiodrf-item-schema"])
    assert item["type"] == "object"
    assert item["properties"]["children"]["items"] == stream["x-aiodrf-item-schema"]
    jsonschema.validate(
        {"label": "parent", "children": [{"label": "leaf", "children": []}]},
        {**stream["x-aiodrf-item-schema"], "components": document["components"]},
    )


@pytest.mark.parametrize(
    "item", [None, dict, {}, StreamItemSerializer(many=True), StreamItemModel]
)
def test_invalid_item_annotation_fails_early(item):
    with pytest.raises(TypeError, match="requires a serializer"):
        StreamSchema(item)


def test_request_annotation_is_rejected():
    class View(APIView):
        @extend_schema(request=StreamSchema(StreamItemSerializer), responses=None)
        async def post(self, request):
            raise AssertionError("Schema generation must not call the view.")

    with pytest.raises(ValueError, match="only supported in extend_schema"):
        SchemaGenerator(patterns=[path("", View.as_view())]).get_schema(public=True)


def test_annotation_cannot_silently_serialize_empty_data():
    annotation = StreamSchema(StreamItemSerializer)
    with pytest.raises(TypeError, match="not a serializer"):
        annotation.to_representation({"value": 1})
    with pytest.raises(TypeError, match="not a serializer"):
        annotation.to_internal_value({"value": 1})
    with pytest.raises(TypeError, match="not a serializer"):
        _ = annotation.data


async def test_schema_and_swagger_permissions_remain_application_owned():
    class View(APIView):
        @extend_schema(
            responses={(200, "text/event-stream"): StreamSchema(StreamItemSerializer)}
        )
        async def get(self, request):
            raise AssertionError("Schema generation must not call the view.")

    urls = ModuleType("protected_stream_schema_urls")
    urls.urlpatterns = [
        path("items/", View.as_view()),
        path(
            "schema/",
            SpectacularAPIView.as_view(permission_classes=[IsAuthenticated]),
            name="stream-schema",
        ),
        path(
            "docs/",
            SpectacularSwaggerView.as_view(
                url_name="stream-schema", permission_classes=[IsAuthenticated]
            ),
        ),
    ]
    with override_settings(ROOT_URLCONF=urls):
        client = AsyncAPIClient()
        assert (await client.get("/schema/")).status_code == 403
        assert (await client.get("/docs/")).status_code == 403
        client.force_authenticate(user=User(username="reader"))
        schema = await client.get(
            "/schema/", HTTP_ACCEPT="application/vnd.oai.openapi+json"
        )
        assert schema.status_code == 200
        assert "StreamItemStream" in schema.data["components"]["schemas"]
        assert (await client.get("/docs/")).status_code == 200
