import datetime
import decimal
import json
import sys
import types
import typing
import uuid

import msgspec
import pydantic
import pytest
from django.test import TestCase, override_settings
from django.urls import path
from fastdrf import compiler
from fastdrf.msgspec.parsers import MsgspecJSONParser
from fastdrf.msgspec.renderers import MsgspecJSONRenderer
from rest_framework import serializers as drf_serializers
from rest_framework.exceptions import ValidationError

from aiodrf import aio, generics, viewsets
from aiodrf.contrib.msgspec import MsgspecSerializer
from aiodrf.contrib.pydantic import PydanticSerializer
from aiodrf.routers import SimpleRouter
from aiodrf.test import AsyncAPIClient
from tests.base import list_errors
from tests.testapp.models import Author, Book, Edition

# -- Schemas ------------------------------------------------------------------


class AuthorIn(msgspec.Struct, forbid_unknown_fields=True):
    name: str


class AuthorOut(msgspec.Struct):
    id: int
    name: str


class TagRef(msgspec.Struct):
    name: str


class Shelf(msgspec.Struct):
    title: str
    tags: list[TagRef]
    note: str | None = None


class AuthorModel(pydantic.BaseModel):
    id: int | None = None
    name: str = pydantic.Field(max_length=20, description="Display name")


class ShelfModel(pydantic.BaseModel):
    title: str
    tags: list[AuthorModel]


class MsgspecAuthorSerializer(MsgspecSerializer):
    class Meta:
        input_schema = AuthorIn
        output_schema = AuthorOut
        model = Author


class PydanticAuthorSerializer(PydanticSerializer):
    class Meta:
        schema = AuthorModel
        model = Author


class MsgspecAuthorViewSet(viewsets.ModelViewSet):
    queryset = Author.objects.all()
    serializer_class = MsgspecAuthorSerializer


class PydanticAuthorViewSet(viewsets.ModelViewSet):
    queryset = Author.objects.all()
    serializer_class = PydanticAuthorSerializer


class BareStructView(generics.ListAPIView):
    queryset = Author.objects.all()
    serializer_class = AuthorOut


class ShelfView(generics.CreateAPIView):
    serializer_class = Shelf

    async def create(self, request, *args, **kwargs):
        from aiodrf import aio
        from aiodrf.response import Response

        serializer = self.get_serializer(data=await request.adata())
        await aio.is_valid(serializer, raise_exception=True)
        return Response({"tags": len(serializer.validated_data["tags"])})


router = SimpleRouter()
router.register("ms-authors", MsgspecAuthorViewSet, basename="ms-author")
router.register("pd-authors", PydanticAuthorViewSet, basename="pd-author")

urlpatterns = [
    *router.urls,
    path("bare/", BareStructView.as_view()),
    path("shelf/", ShelfView.as_view()),
]


# -- Adapters -----------------------------------------------------------------


@override_settings(ROOT_URLCONF=__name__)
class AdapterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.author = Author.objects.create(name="Ursula")

    def setUp(self):
        self.client = AsyncAPIClient()

    async def test_msgspec_crud(self):
        response = await self.client.post("/ms-authors/", {"name": "Iain"})
        assert response.status_code == 201, response.data
        assert set(response.data) == {"id", "name"}
        pk = response.data["id"]

        response = await self.client.get("/ms-authors/")
        assert [a["name"] for a in response.data] == ["Ursula", "Iain"]

        response = await self.client.patch(f"/ms-authors/{pk}/", {"name": "Banks"})
        assert response.data == {"id": pk, "name": "Banks"}

        response = await self.client.post("/ms-authors/", {"name": 3, "x": 1})
        assert response.status_code == 400
        assert response.data == {"name": ["Expected `str`, got `int`"]}

        response = await self.client.post("/ms-authors/", {})
        assert response.data == {"name": ["This field is required."]}
        assert response.data["name"][0].code == "required"

    async def test_pydantic_crud(self):
        response = await self.client.post("/pd-authors/", {"name": "Iain"})
        assert response.status_code == 201, response.data
        response = await self.client.post("/pd-authors/", {"name": "x" * 30})
        assert response.status_code == 400
        assert response.data["name"][0].code == "string_too_long"
        response = await self.client.patch(f"/pd-authors/{self.author.pk}/", {})
        assert response.data == {"id": self.author.pk, "name": "Ursula"}

    async def test_bare_schema_class_as_serializer_class(self):
        response = await self.client.get("/bare/")
        assert response.data == [{"id": self.author.pk, "name": "Ursula"}]

    async def test_nested_errors(self):
        response = await self.client.post(
            "/shelf/", {"title": "t", "tags": [{"name": "a"}, {}]}
        )
        assert response.status_code == 400
        expected = list_errors({1: {"name": ["This field is required."]}}, 2)
        assert response.data == {"tags": expected}
        response = await self.client.post(
            "/shelf/", {"title": "t", "tags": [{"name": "a"}]}
        )
        assert response.data == {"tags": 1}

    async def test_options_metadata_uses_synthetic_fields(self):
        response = await self.client.options("/ms-authors/")
        assert response.status_code == 200


def test_pydantic_errors_are_collected():
    with pytest.raises(ValidationError) as info:
        PydanticSerializer().backend.validate(
            ShelfModel, {"tags": [{"name": 1}, {}]}, partial=False, strict=False
        )
    detail = info.value.detail
    assert set(detail) == {"title", "tags"}
    assert detail["tags"][0]["name"][0].code == "string_type"
    assert detail["tags"][1]["name"][0] == "This field is required."


@override_settings(REST_FRAMEWORK={"LIST_SERIALIZER_ERRORS_AS_DICT": False})
def test_list_errors_follow_drf_setting():
    from fastdrf.typed import error_detail

    detail = error_detail([(("tags", 1, "name"), "Required.", "required")])
    assert detail == {"tags": [{}, {"name": ["Required."]}]}


# -- OpenAPI --------------------------------------------------------------------


@override_settings(ROOT_URLCONF=__name__)
class SchemaTests(TestCase):
    def schema(self):
        from drf_spectacular.generators import SchemaGenerator

        return SchemaGenerator().get_schema(request=None, public=True)

    def test_components_come_from_schema_classes(self):
        schema = self.schema()
        components = schema["components"]["schemas"]
        assert components["AuthorIn"]["properties"] == {"name": {"type": "string"}}
        assert components["AuthorIn"]["additionalProperties"] is False
        assert components["AuthorOut"]["required"] == ["id", "name"]
        create = schema["paths"]["/ms-authors/"]["post"]
        assert create["requestBody"]["content"]["application/json"]["schema"][
            "$ref"
        ].endswith("/AuthorIn")
        assert create["responses"]["201"]["content"]["application/json"]["schema"][
            "$ref"
        ].endswith("/AuthorOut")
        # Nested structs are registered as components.
        assert "TagRef" in components
        # PATCH bodies are not required fields.
        assert "required" not in components["PatchedAuthorIn"]

    def test_pydantic_directions_and_nullable(self):
        components = self.schema()["components"]["schemas"]
        name = components["AuthorModel"]["properties"]["name"]
        assert name["maxLength"] == 20
        assert name["description"] == "Display name"
        # OpenAPI 3.0: ``anyOf [int, null]`` becomes ``nullable``.
        assert components["AuthorModel"]["properties"]["id"]["nullable"] is True
        assert components["Shelf"]["properties"]["note"] == {
            "type": "string",
            "nullable": True,
            "default": None,
        }


# -- Renderer / parser ----------------------------------------------------------


def test_renderer_matches_drf_json_for_common_data():
    from rest_framework.exceptions import ErrorDetail
    from rest_framework.renderers import JSONRenderer

    data = {
        "detail": ErrorDetail("Nope", code="x"),
        "when": datetime.datetime(2024, 1, 2, 3, 4, 5, tzinfo=datetime.UTC),
        "id": uuid.UUID(int=1),
        "price": decimal.Decimal("1.5"),
        "text": "a\u2028b",  # LINE SEPARATOR: valid in JSON, a line break in JavaScript
        "items": [1, 2.5, None, True],
    }
    assert MsgspecJSONRenderer().render(data) == JSONRenderer().render(data)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (datetime.timedelta(hours=1), b'"PT3600S"'),
        (b"ab", b'"YWI="'),
        (b"\xff\xfe", b'"//4="'),
        (float("nan"), b"null"),
        (float("inf"), b"null"),
        ([decimal.Decimal("1.50"), decimal.Decimal(2)], b"[1.50,2]"),
        ([decimal.Decimal("-0"), decimal.Decimal("1E+30")], b"[-0,1E+30]"),
        ([1e300, 1e-7], b"[1e300,1e-7]"),
        (datetime.time(1, tzinfo=datetime.UTC), b'"01:00:00Z"'),
    ],
    ids=repr,
)
def test_the_documented_differences_from_drfs_encoder(value, expected):
    # docs/guides/msgspec-pydantic.md, section 3.
    assert MsgspecJSONRenderer().render(value) == expected


def test_an_unsupported_value_fails_as_in_drf():
    from rest_framework.renderers import JSONRenderer

    message = "Object of type object is not JSON serializable"
    for renderer in (JSONRenderer(), MsgspecJSONRenderer()):
        with pytest.raises(TypeError, match=message):
            renderer.render({"value": object()})


@pytest.mark.parametrize(
    "data",
    [
        {True: 1, None: 2},  # json stringifies these keys; msgspec refuses them
        {"counts": {False: 3}},
        {"note": "NaN", "limit": "Infinity"},  # the words, in strings
        {"price": decimal.Decimal("1.5"), "none": "NaN"},
    ],
    ids=repr,
)
def test_the_renderer_outputs_drfs_bytes_where_msgspec_cannot(data):
    from rest_framework.renderers import JSONRenderer

    assert MsgspecJSONRenderer().render(data) == JSONRenderer().render(data)


@pytest.mark.parametrize(
    "value",
    [
        decimal.Decimal("NaN"),
        decimal.Decimal("Infinity"),
        decimal.Decimal("-Infinity"),
        decimal.Decimal("sNaN"),
    ],
    ids=str,
)
def test_a_non_finite_decimal_fails_as_in_drf(value):
    # msgspec writes the bare token, which no JSON parser reads.
    from rest_framework.renderers import JSONRenderer

    message = "Out of range float values|cannot convert signaling NaN"
    with pytest.raises(ValueError, match=message) as drf:
        JSONRenderer().render({"value": value})
    with pytest.raises(ValueError, match=message) as ours:
        MsgspecJSONRenderer().render({"value": value})
    assert str(ours.value) == str(drf.value)


def test_parser():
    import io

    from rest_framework.exceptions import ParseError

    assert MsgspecJSONParser().parse(io.BytesIO(b'{"a": [1]}')) == {"a": [1]}
    with pytest.raises(ParseError):
        MsgspecJSONParser().parse(io.BytesIO(b"{"))


# -- Compiler -----------------------------------------------------------------


class EditionSerializer(drf_serializers.ModelSerializer):
    class Meta:
        model = Edition
        exclude = ["price"]


class BookBriefSerializer(drf_serializers.ModelSerializer):
    class Meta:
        model = Book
        fields = ["id", "title", "author"]


class EditionWithBookSerializer(drf_serializers.ModelSerializer):
    book = BookBriefSerializer()

    class Meta:
        model = Edition
        fields = ["id", "code", "book", "published"]


class EditionWithDecimal(drf_serializers.ModelSerializer):
    class Meta:
        model = Edition
        fields = ["id", "price"]


class CompilerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        author = Author.objects.create(name="Ursula")
        book = Book.objects.create(title="Dispossessed", isbn="1", author=author)
        cls.editions = [
            Edition.objects.create(
                code=uuid.uuid4(),
                book=book,
                translator=author if i else None,
                published=datetime.datetime(
                    2024, 1, 2, 3, 4, 5, i * 1000, tzinfo=datetime.UTC
                ),
                released=datetime.date(2024, 1, 2) if i else None,
                active=bool(i),
                rating=4.5 if i else None,
                price=decimal.Decimal("12.50"),
                format="hb",
                extra={"k": [1, 2]} if i else {},
                notes="",
            )
            for i in range(2)
        ]

    def fetch(self):
        return list(Edition.objects.select_related("book").order_by("id"))

    def test_strict_parity(self):
        for serializer_class in (EditionSerializer, EditionWithBookSerializer):
            # What the database returns is DRF's output in "fast" parity too;
            # "strict" leaves JSON to DRF for other values.
            if serializer_class is EditionSerializer:
                assert "extra is a JSONField" in compiler.report(serializer_class())
            else:
                assert compiler.report(serializer_class()) is None
            spec = compiler.analyze(serializer_class(), "fast")
            for backend in ("msgspec", "pydantic"):
                encoder = compiler._build(backend, spec)
                expected = serializer_class(self.fetch(), many=True).data
                assert encoder.dump_many(self.fetch()) == json.loads(
                    json.dumps(expected)
                ), backend
                assert encoder.dump(self.fetch()[1]) == json.loads(
                    json.dumps(serializer_class(self.fetch()[1]).data)
                )

    def test_not_compilable_reasons(self):
        from tests.testapp.serializers import BookSerializer

        # The primary keys of its tags are DRF's ManyRelatedField output.
        assert compiler.report(BookSerializer()) is None

        class WithDepth(EditionWithBookSerializer):
            book = None

            class Meta(EditionWithBookSerializer.Meta):
                depth = 1

        assert compiler.report(WithDepth()) is None
        # "strict" quantizes as DRF does, with DRF's own code; a Decimal DRF
        # leaves to the renderer's encoder stays on DRF.
        assert compiler.report(EditionWithDecimal()) is None
        with override_settings(REST_FRAMEWORK={"COERCE_DECIMAL_TO_STRING": False}):
            assert "output as a Decimal" in compiler.report(EditionWithDecimal())
        assert compiler.report(EditionWithDecimal(), parity="fast") is None

    @override_settings(FASTDRF={"SERIALIZER_BACKEND": "msgspec"}, AIODRF={})
    async def test_backend_setting_in_views(self):
        view = generics.ListAPIView.as_view(
            queryset=Edition.objects.select_related("book"),
            serializer_class=EditionSerializer,
        )
        from aiodrf.test import AsyncAPIRequestFactory

        response = await view(AsyncAPIRequestFactory().get("/"))
        expected = await __import__("asgiref.sync").sync.sync_to_async(
            lambda: json.loads(
                json.dumps(EditionSerializer(self.fetch(), many=True).data)
            )
        )()
        assert json.loads(response.render().content) == expected

    @override_settings(TIME_ZONE="Europe/Istanbul")
    def test_datetimes_follow_the_current_time_zone(self):
        from django.utils import timezone

        class Compiled(EditionWithBookSerializer):
            class Meta(EditionWithBookSerializer.Meta):
                serializer_backend = "msgspec"
                serializer_backend_fallback = "error"

        with timezone.override("Europe/Istanbul"):
            expected = EditionWithBookSerializer(self.fetch(), many=True).data
            assert compiler.compiled_for(Compiled(self.fetch(), many=True))
            assert aio.try_data(Compiled(self.fetch(), many=True)) == expected
        assert expected[0]["published"].endswith("+03:00")


def test_nested_components_that_share_a_name_are_reported():
    # drf-spectacular compares the ``object`` of components with one name. A
    # string there (the name again) would make every pair look identical.
    from types import SimpleNamespace

    from drf_spectacular.drainage import GENERATOR_STATS
    from drf_spectacular.plumbing import ComponentRegistry
    from fastdrf.spectacular import SchemaSerializerExtension

    def order_serializer(part_fields):
        part = msgspec.defstruct("Part", part_fields)
        order = msgspec.defstruct("Order", [("part", part)])
        meta = type("Meta", (), {"schema": order})
        return type("OrderSerializer", (MsgspecSerializer,), {"Meta": meta})()

    def warnings_for(*serializers):
        GENERATOR_STATS.reset()
        auto_schema = SimpleNamespace(registry=ComponentRegistry())
        for serializer in serializers:
            SchemaSerializerExtension(serializer).map_serializer(
                auto_schema, "response"
            )
        try:
            return [
                message
                for message in GENERATOR_STATS._warn_cache
                if "identical names" in message
            ]
        finally:
            GENERATOR_STATS.reset()

    same = warnings_for(
        order_serializer([("sku", str)]), order_serializer([("sku", str)])
    )
    assert same == []
    different = warnings_for(
        order_serializer([("sku", str)]), order_serializer([("sku", int)])
    )
    assert len(different) == 1
    assert '"Part"' in different[0]


# -- Request and response shapes of one schema class --------------------------------


class AliasedItem(pydantic.BaseModel):
    value: int = pydantic.Field(serialization_alias="public_value")


class AliasedEnvelope(pydantic.BaseModel):
    item: AliasedItem


class AliasedItemSerializer(PydanticSerializer):
    class Meta:
        schema = AliasedItem


class AliasedEnvelopeSerializer(PydanticSerializer):
    class Meta:
        schema = AliasedEnvelope


class AliasedItemView(generics.CreateAPIView):
    serializer_class = AliasedItemSerializer
    authentication_classes = []
    permission_classes = []


class AliasedEnvelopeView(generics.CreateAPIView):
    serializer_class = AliasedEnvelopeSerializer
    authentication_classes = []
    permission_classes = []


aliased = types.ModuleType(f"{__name__}.aliased")
aliased.urlpatterns = [
    path("items/", AliasedItemView.as_view()),
    path("envelopes/", AliasedEnvelopeView.as_view()),
]
sys.modules[aliased.__name__] = aliased


class DirectionTests(TestCase):
    """The schema describes what the API accepts and what it returns, separately."""

    def schema(self, split):
        from unittest import mock

        from drf_spectacular.drainage import GENERATOR_STATS
        from drf_spectacular.generators import SchemaGenerator
        from drf_spectacular.settings import spectacular_settings

        GENERATOR_STATS.reset()
        with (
            override_settings(ROOT_URLCONF=f"{__name__}.aliased"),
            mock.patch.object(spectacular_settings, "COMPONENT_SPLIT_REQUEST", split),
        ):
            schema = SchemaGenerator().get_schema(request=None, public=True)
        collisions = [w for w in GENERATOR_STATS._warn_cache if "identical names" in w]
        GENERATOR_STATS.reset()
        return schema, collisions

    def assert_directions(self, schema):
        components = schema["components"]["schemas"]
        for request_name, response_name in (
            ("AliasedItemRequest", "AliasedItem"),
            ("AliasedEnvelopeRequest", "AliasedEnvelope"),
        ):
            assert request_name in components, sorted(components)
            assert response_name in components, sorted(components)
        assert list(components["AliasedItemRequest"]["properties"]) == ["value"]
        assert list(components["AliasedItem"]["properties"]) == ["public_value"]
        request_item = components["AliasedEnvelopeRequest"]["properties"]["item"][
            "$ref"
        ]
        response_item = components["AliasedEnvelope"]["properties"]["item"]["$ref"]
        assert request_item.endswith("/AliasedItemRequest")
        assert response_item.endswith("/AliasedItem")
        operation = schema["paths"]["/envelopes/"]["post"]
        body = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
        response = operation["responses"]["201"]["content"]["application/json"][
            "schema"
        ]["$ref"]
        assert body.endswith("/AliasedEnvelopeRequest")
        assert response.endswith("/AliasedEnvelope")
        # Both are what the runtime does.
        serializer = AliasedEnvelopeSerializer(data={"item": {"value": 1}})
        assert serializer.is_valid(), serializer.errors
        assert AliasedEnvelopeSerializer(serializer.validated_data).data == {
            "item": {"public_value": 1}
        }

    def test_with_split_components(self):
        schema, collisions = self.schema(split=True)
        self.assert_directions(schema)
        assert collisions == []

    def test_without_split_components(self):
        # The same names, chosen by aiodrf, and a warning that says so.
        schema, collisions = self.schema(split=False)
        self.assert_directions(schema)
        assert collisions == []


@pytest.mark.parametrize(
    ("encoding", "body"),
    [("utf-16", b"\xff"), ("utf-32", b"\x00\x00"), ("utf-8", b"\xff"), ("utf-8", b"{")],
)
def test_the_msgspec_parser_answers_bad_bodies_as_drf(encoding, body):
    import io

    from fastdrf.msgspec.parsers import MsgspecJSONParser
    from rest_framework.exceptions import ParseError
    from rest_framework.parsers import JSONParser

    for parser in (JSONParser(), MsgspecJSONParser()):
        with pytest.raises(ParseError, match="JSON parse error"):
            parser.parse(io.BytesIO(body), parser_context={"encoding": encoding})


async def test_a_body_its_charset_cannot_decode_is_a_400():
    from fastdrf.msgspec.parsers import MsgspecJSONParser

    from aiodrf.response import Response
    from aiodrf.test import AsyncAPIRequestFactory
    from aiodrf.views import APIView

    class Echo(APIView):
        authentication_classes = []
        permission_classes = []
        parser_classes = [MsgspecJSONParser]

        async def post(self, request):
            return Response(await request.adata())

    request = AsyncAPIRequestFactory().generic(
        "POST", "/", b"\xff", content_type="application/json; charset=utf-16"
    )
    response = await Echo.as_view()(request)
    assert response.status_code == 400
    assert str(response.data["detail"]).startswith("JSON parse error")


def test_an_unknown_charset_is_handled_as_by_drf():
    import io

    from django.http import HttpRequest
    from fastdrf.msgspec.parsers import MsgspecJSONParser
    from rest_framework.parsers import JSONParser

    # Django ignores a charset it does not know: the parser gets the default.
    request = HttpRequest()
    request.META["CONTENT_TYPE"] = "application/json; charset=not-a-codec"
    request._set_content_type_params(request.META)
    assert request.encoding is None
    # Given one directly, both parsers let the LookupError through.
    for parser in (JSONParser(), MsgspecJSONParser()):
        with pytest.raises(LookupError):
            parser.parse(io.BytesIO(b"{}"), parser_context={"encoding": "not-a-codec"})


# -- OpenAPI 3.0 and 3.1 documents that validate --------------------------------


class Order(pydantic.BaseModel):
    quantity: int = pydantic.Field(gt=0, lt=100)
    kind: typing.Literal["retail"]
    point: tuple[int, str]


class Page[T](pydantic.BaseModel):
    items: list[T]


class Quantity(msgspec.Struct):
    value: typing.Annotated[int, msgspec.Meta(gt=0)]


def _view(schema_class, name):
    class Serializer(
        PydanticSerializer
        if issubclass(schema_class, pydantic.BaseModel)
        else MsgspecSerializer
    ):
        class Meta:
            schema = schema_class

    class View(generics.GenericAPIView):
        authentication_classes = []
        permission_classes = []
        serializer_class = Serializer

        async def post(self, request):
            return None

    return path(f"{name}/", View.as_view())


versions = types.ModuleType(f"{__name__}.versions")
versions.urlpatterns = [
    _view(Order, "orders"),
    _view(Page[int], "pages"),
    _view(Quantity, "quantities"),
]
sys.modules[versions.__name__] = versions


@pytest.mark.parametrize("version", ["3.0.3", "3.1.0"])
def test_the_document_is_valid_openapi(version):
    from drf_spectacular.generators import SchemaGenerator
    from drf_spectacular.settings import patched_settings
    from drf_spectacular.validation import validate_schema

    with (
        override_settings(ROOT_URLCONF=versions.__name__),
        patched_settings({"OAS_VERSION": version}),
    ):
        schema = SchemaGenerator().get_schema(request=None, public=True)
    validate_schema(schema)
    components = schema["components"]["schemas"]
    assert "Page_int_" in components, sorted(components)
    quantity = components["Order"]["properties"]["quantity"]
    if version == "3.0.3":
        assert quantity["minimum"] == 0
        assert quantity["exclusiveMinimum"] is True
        assert '"const"' not in json.dumps(schema)
    else:
        assert quantity["exclusiveMinimum"] == 0


# -- Review findings: component identity, names and the 3.0 dialect -------------


def _document(urlpatterns, **settings):
    from drf_spectacular.drainage import GENERATOR_STATS
    from drf_spectacular.generators import SchemaGenerator
    from drf_spectacular.settings import patched_settings

    module = types.ModuleType(f"{__name__}.document")
    module.urlpatterns = urlpatterns
    sys.modules[module.__name__] = module
    GENERATOR_STATS.reset()
    try:
        with (
            override_settings(ROOT_URLCONF=module.__name__),
            patched_settings(settings),
        ):
            schema = SchemaGenerator().get_schema(request=None, public=True)
        return schema, list(GENERATOR_STATS._warn_cache)
    finally:
        GENERATOR_STATS.reset()
        del sys.modules[module.__name__]


def _body(schema, route, method):
    content = schema["paths"][route][method]["requestBody"]["content"]
    return content["application/json"]["schema"]["$ref"].rsplit("/", 1)[1]


class AuthorPatchView(generics.UpdateAPIView):
    queryset = Author.objects.all()
    serializer_class = MsgspecAuthorSerializer
    http_method_names = ["patch"]


class AuthorCreateView(generics.CreateAPIView):
    queryset = Author.objects.all()
    serializer_class = MsgspecAuthorSerializer


def test_a_partial_body_does_not_replace_the_full_one():
    # The PATCH is documented first; POST must still require ``name``.
    schema, _ = _document(
        [
            path("a-patch/<int:pk>/", AuthorPatchView.as_view()),
            path("b-create/", AuthorCreateView.as_view()),
        ],
        COMPONENT_SPLIT_PATCH=False,
    )
    components = schema["components"]["schemas"]
    create = components[_body(schema, "/b-create/", "post")]
    assert create["required"] == ["name"]


class Named(pydantic.BaseModel):
    value: int = pydantic.Field(serialization_alias="public_value")


class NamedRequest(pydantic.BaseModel):
    other: str


class NamedHolder(pydantic.BaseModel):
    named: Named
    request: NamedRequest


class NamedHolderSerializer(PydanticSerializer):
    class Meta:
        schema = NamedHolder


class NamedHolderView(generics.CreateAPIView):
    serializer_class = NamedHolderSerializer
    authentication_classes = []
    permission_classes = []


def test_a_renamed_request_component_keeps_a_component_of_that_name():
    schema, _ = _document(
        [path("holders/", NamedHolderView.as_view())], COMPONENT_SPLIT_REQUEST=True
    )
    components = schema["components"]["schemas"]
    holder = components[_body(schema, "/holders/", "post")]["properties"]
    named = holder["named"]["$ref"].rsplit("/", 1)[1]
    request = holder["request"]["$ref"].rsplit("/", 1)[1]
    assert named != request
    assert list(components[named]["properties"]) == ["value"]
    assert list(components[request]["properties"]) == ["other"]


class Keywords(pydantic.BaseModel):
    const: int
    prefixItems: str
    exclusiveMinimum: str
    tag: typing.Literal["x"] = "x"


class KeywordsSerializer(PydanticSerializer):
    class Meta:
        schema = Keywords


class KeywordsView(generics.CreateAPIView):
    serializer_class = KeywordsSerializer
    authentication_classes = []
    permission_classes = []


def test_field_names_that_are_keywords_stay_field_names_in_openapi_30():
    from drf_spectacular.validation import validate_schema

    schema, _ = _document(
        [path("keywords/", KeywordsView.as_view())], OAS_VERSION="3.0.3"
    )
    validate_schema(schema)
    properties = schema["components"]["schemas"]["Keywords"]["properties"]
    assert list(properties) == ["const", "prefixItems", "exclusiveMinimum", "tag"]
    assert properties["const"]["type"] == "integer"
    # A ``const`` value is still converted (spectacular names the enum).
    assert schema["components"]["schemas"]["TagEnum"]["enum"] == ["x"]


class Maybe(pydantic.BaseModel):
    author: AuthorModel | None = None


class MaybeSerializer(PydanticSerializer):
    class Meta:
        schema = Maybe


class DRFMaybeSerializer(drf_serializers.Serializer):
    author = PydanticAuthorSerializer(allow_null=True)


class MaybeView(generics.CreateAPIView):
    serializer_class = MaybeSerializer
    authentication_classes = []
    permission_classes = []


class DRFMaybeView(MaybeView):
    serializer_class = DRFMaybeSerializer


def test_a_nullable_reference_is_written_as_spectacular_writes_one():
    schema, _ = _document(
        [path("maybe/", MaybeView.as_view()), path("drf/", DRFMaybeView.as_view())],
        OAS_VERSION="3.0.3",
    )
    components = schema["components"]["schemas"]
    ours = components["Maybe"]["properties"]["author"]
    spectaculars = components["DRFMaybe"]["properties"]["author"]
    assert ours["nullable"] is spectaculars["nullable"] is True
    assert ours["allOf"] == spectaculars["allOf"]
