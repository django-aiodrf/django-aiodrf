"""DRF field eligibility and schema-native types, independently for each engine.

These two parametrized tests check input recognition and compiled output, not
just successful validation through a fallback. Vendor schemas have a separate
contract: their values and JSON output must agree with the selected engine.
"""

import datetime
import decimal
import enum
import ipaddress
import json
import uuid
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Any, Literal, NamedTuple, NewType

import msgspec
import pydantic
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import fields, relations, serializers
from rest_framework.renderers import JSONRenderer
from typing_extensions import TypedDict

from aiodrf.contrib import compiler, inputs
from aiodrf.contrib.msgspec.compiler import build as build_msgspec
from aiodrf.contrib.msgspec.serializers import serializer_for as msgspec_serializer
from aiodrf.contrib.pydantic.compiler import build as build_pydantic
from aiodrf.contrib.pydantic.serializers import serializer_for as pydantic_serializer
from tests.test_inputs import exact
from tests.testapp.models import Author

pytestmark = pytest.mark.unit


@dataclass(frozen=True)
class FieldCase:
    name: str
    field: fields.Field
    value: Any
    recognized: bool
    output: bool = False


@dataclass(frozen=True)
class SchemaCase:
    name: str
    annotation: Any
    value: Any
    strict: bool = True


class State(enum.Enum):
    ready = "ready"


class Number(enum.IntEnum):
    one = 1


class Label(enum.StrEnum):
    ready = "ready"


class Flags(enum.IntFlag):
    read = 1
    write = 2


class Position(NamedTuple):
    x: int
    y: int


class Coordinates(TypedDict):
    x: int
    y: int


@dataclass
class Point:
    x: int
    y: int


class Nested(serializers.Serializer):
    number = serializers.IntegerField()


FIELD_CASES = [
    FieldCase("BooleanField", fields.BooleanField(), True, True, True),
    FieldCase("IntegerField", fields.IntegerField(), 12, True, True),
    FieldCase("FloatField", fields.FloatField(), 1.25, True, True),
    FieldCase("CharField", fields.CharField(), "value", True, True),
    FieldCase("EmailField", fields.EmailField(), "user@example.com", False, True),
    FieldCase("RegexField", fields.RegexField(r"^a+$"), "aaa", False, True),
    FieldCase("SlugField", fields.SlugField(), "a-slug", False, True),
    FieldCase("URLField", fields.URLField(), "https://example.com/", False, True),
    FieldCase("UUIDField", fields.UUIDField(), uuid.UUID(int=1), True, True),
    FieldCase("IPAddressField", fields.IPAddressField(), "127.0.0.1", False, True),
    FieldCase(
        "DecimalField", fields.DecimalField(6, 2), decimal.Decimal("1.25"), False, True
    ),
    FieldCase(
        "DateTimeField",
        fields.DateTimeField(),
        datetime.datetime(2026, 9, 27, tzinfo=datetime.UTC),
        False,
        True,
    ),
    FieldCase("DateField", fields.DateField(), datetime.date(2026, 9, 27), True, True),
    FieldCase("TimeField", fields.TimeField(), datetime.time(12, 30), True, True),
    FieldCase(
        "DurationField", fields.DurationField(), datetime.timedelta(seconds=90), False
    ),
    FieldCase("ChoiceField", fields.ChoiceField(["one", "two"]), "one", True, True),
    FieldCase(
        "MultipleChoiceField",
        fields.MultipleChoiceField(choices=["one", "two"]),
        ["one"],
        False,
    ),
    FieldCase(
        "FilePathField",
        fields.FilePathField(path=str(Path(__file__).parent)),
        "example.txt",
        False,
        True,
    ),
    FieldCase(
        "FileField", fields.FileField(), SimpleUploadedFile("a.txt", b"a"), False
    ),
    FieldCase(
        "ImageField", fields.ImageField(), SimpleUploadedFile("a.png", b"image"), False
    ),
    FieldCase("ListField", fields.ListField(child=fields.IntegerField()), [1, 2], True),
    FieldCase(
        "DictField", fields.DictField(child=fields.IntegerField()), {"one": 1}, True
    ),
    FieldCase("HStoreField", fields.HStoreField(), {"one": "1", "two": None}, True),
    FieldCase(
        "JSONField", fields.JSONField(), {"values": [1, True, None]}, False, True
    ),
    FieldCase("ReadOnlyField", fields.ReadOnlyField(), 1, True),
    FieldCase("HiddenField", fields.HiddenField(default=12), 1, False, True),
    FieldCase("SerializerMethodField", fields.SerializerMethodField(), 1, True),
    FieldCase(
        "ModelField", fields.ModelField(Author._meta.get_field("name")), "name", False
    ),
    FieldCase(
        "PrimaryKeyRelatedField",
        serializers.PrimaryKeyRelatedField(queryset=Author.objects.all()),
        1,
        False,
    ),
    FieldCase(
        "SlugRelatedField",
        serializers.SlugRelatedField(slug_field="name", queryset=Author.objects.all()),
        "name",
        False,
    ),
    FieldCase("StringRelatedField", serializers.StringRelatedField(), "name", True),
    FieldCase(
        "HyperlinkedRelatedField",
        serializers.HyperlinkedRelatedField(
            view_name="author-detail", queryset=Author.objects.all()
        ),
        "/authors/1/",
        False,
    ),
    FieldCase(
        "HyperlinkedIdentityField",
        serializers.HyperlinkedIdentityField(view_name="author-detail"),
        None,
        True,
    ),
    FieldCase(
        "ManyRelatedField",
        serializers.PrimaryKeyRelatedField(many=True, queryset=Author.objects.all()),
        [1],
        False,
    ),
    FieldCase("Serializer", Nested(), {"number": 1}, True),
    FieldCase("ListSerializer", Nested(many=True), [{"number": 1}], True),
]
if hasattr(fields, "BigIntegerField"):
    FIELD_CASES.append(
        FieldCase(
            "BigIntegerField",
            fields.BigIntegerField(coerce_to_string=False),
            2**40,
            True,
            True,
        )
    )

NATIVE_CASES = [
    SchemaCase("none", type(None), None),
    SchemaCase("bool", bool, True),
    SchemaCase("int", int, 12),
    SchemaCase("float", float, 1.25),
    SchemaCase("str", str, "text"),
    SchemaCase("bytes", bytes, b"text"),
    SchemaCase("list", list[int], [1, 2]),
    SchemaCase("dict", dict[str, int], {"one": 1}),
    SchemaCase("tuple", tuple[int, str], (1, "one")),
    SchemaCase("variable_tuple", tuple[int, ...], (1, 2)),
    SchemaCase("set", set[int], {1}),
    SchemaCase("frozenset", frozenset[int], frozenset({1})),
    SchemaCase(
        "datetime",
        datetime.datetime,
        datetime.datetime(2026, 9, 27, tzinfo=datetime.UTC),
    ),
    SchemaCase("date", datetime.date, datetime.date(2026, 9, 27)),
    SchemaCase("time", datetime.time, datetime.time(12, 30)),
    SchemaCase("duration", datetime.timedelta, datetime.timedelta(seconds=90)),
    SchemaCase("uuid", uuid.UUID, uuid.UUID(int=1)),
    SchemaCase("decimal", decimal.Decimal, decimal.Decimal("1.250")),
    SchemaCase("enum", State, State.ready),
    SchemaCase("int_enum", Number, Number.one),
    SchemaCase("str_enum", Label, Label.ready),
    SchemaCase("int_flag", Flags, Flags.read | Flags.write),
    SchemaCase("dataclass", Point, Point(1, 2)),
    SchemaCase("named_tuple", Position, Position(1, 2)),
    SchemaCase("typed_dict", Coordinates, {"x": 1, "y": 2}),
    SchemaCase("any", Any, {"x": [1, True, None]}),
    SchemaCase("optional", int | None, None),
    SchemaCase("union", int | str, "one"),
    SchemaCase("literal", Literal["one", "two"], "one"),
    SchemaCase("new_type", NewType("Identifier", int), 1),
    SchemaCase("sequence", Sequence[int], [1, 2]),
    SchemaCase("mapping", Mapping[str, int], {"one": 1}),
]


def assert_drf_contract(case, backend):
    serializer_class = type(
        "MatrixSerializer", (serializers.Serializer,), {"value": case.field}
    )
    serializer = serializer_class(data={"value": case.value})
    recognized = inputs.recognize(serializer, backend=backend)
    eligibility = inputs.report_input_details(serializer, backend=backend)
    assert (recognized is not inputs.NOT_RECOGNIZED) is case.recognized, eligibility
    if case.recognized:
        assert eligibility.eligible
        assert serializer.is_valid(), serializer.errors
        assert exact(recognized) == exact(serializer.validated_data)
    else:
        assert not eligibility.eligible, case.name

    if case.output:
        spec = compiler.analyze(serializer_class(), parity="fast")
        build = build_msgspec if backend == "msgspec" else build_pydantic
        source = SimpleNamespace(value=case.value)
        # A direct encoder call cannot silently invoke DRF fallback.
        actual = build(spec).dump(source)
        expected = serializer_class(source).data
        assert actual == json.loads(JSONRenderer().render(expected))
    else:
        with pytest.raises(compiler.NotCompilable):
            compiler.analyze(serializer_class(), parity="fast")


@pytest.mark.parametrize("module", [fields, relations])
def test_matrix_tracks_public_drf_field_classes(module):
    concrete = {
        name
        for name, member in vars(module).items()
        if not name.startswith("_")
        and isinstance(member, type)
        and issubclass(member, fields.Field)
        and member.__module__ == module.__name__
        and name not in {"Field", "RelatedField"}
    }
    assert concrete <= {case.name for case in FIELD_CASES}


@pytest.mark.parametrize(
    "case",
    [
        *FIELD_CASES,
        *NATIVE_CASES,
        SchemaCase("bytearray", bytearray, bytearray(b"text")),
        SchemaCase("constrained", Annotated[int, msgspec.Meta(ge=0)], 1),
        SchemaCase("nested_struct", msgspec.defstruct("Child", [("x", int)]), {"x": 1}),
    ],
    ids=lambda case: case.name,
)
def test_msgspec_type_matrix(case):
    if isinstance(case, FieldCase):
        assert_drf_contract(case, "msgspec")
        return
    schema = msgspec.defstruct("Matrix", [("value", case.annotation)])
    data = {"value": case.value}
    reference = msgspec.convert(data, schema, strict=True)
    serializer_class = msgspec_serializer(schema)
    serializer = serializer_class(data=data)
    assert serializer.is_valid(), serializer.errors
    assert exact(serializer.validated_data["value"]) == exact(reference.value)
    assert serializer_class(reference).data == msgspec.to_builtins(reference)
    assert serializer_class([reference], many=True).data == msgspec.to_builtins(
        [reference]
    )


@pytest.mark.parametrize(
    "case",
    [
        *FIELD_CASES,
        *NATIVE_CASES,
        SchemaCase("path", Path, Path("example.txt")),
        SchemaCase("ipv4", ipaddress.IPv4Address, ipaddress.IPv4Address("127.0.0.1")),
        SchemaCase("ipv6", ipaddress.IPv6Address, ipaddress.IPv6Address("::1")),
        SchemaCase(
            "network", ipaddress.IPv4Network, ipaddress.IPv4Network("192.0.2.0/24")
        ),
        SchemaCase("url", pydantic.AnyUrl, pydantic.AnyUrl("https://example.com/")),
        SchemaCase("secret", pydantic.SecretStr, pydantic.SecretStr("example")),
        SchemaCase("deque", deque[int], [1, 2], strict=False),
        SchemaCase("constrained", Annotated[int, pydantic.Field(ge=0)], 1),
        SchemaCase(
            "nested_model", pydantic.create_model("Child", x=(int, ...)), {"x": 1}
        ),
    ],
    ids=lambda case: case.name,
)
def test_pydantic_type_matrix(case):
    if isinstance(case, FieldCase):
        assert_drf_contract(case, "pydantic")
        return
    schema = pydantic.create_model("Matrix", value=(case.annotation, ...))
    data = {"value": case.value}
    reference = schema.model_validate(data, strict=case.strict)
    serializer_class = pydantic_serializer(schema)
    serializer_class.Meta.strict = case.strict
    serializer = serializer_class(data=data)
    assert serializer.is_valid(), serializer.errors
    assert exact(serializer.validated_data["value"]) == exact(reference.value)
    assert serializer_class(reference).data == reference.model_dump(mode="json")
    assert serializer_class([reference], many=True).data == [
        reference.model_dump(mode="json")
    ]
