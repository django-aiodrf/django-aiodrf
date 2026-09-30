"""Native schema hooks, request isolation and non-mapping representations."""

import asyncio
import gc
import threading
import weakref

import msgspec
import pydantic
import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework import fields

from aiodrf.contrib.msgspec import MsgspecSerializer
from aiodrf.contrib.typed import adapt

pytestmark = pytest.mark.unit


class ContextModel(pydantic.BaseModel):
    value: int

    @pydantic.field_validator("value")
    @classmethod
    def add_offset(cls, value, info):
        return value + info.context["offset"]

    @pydantic.field_serializer("value")
    def display(self, value, info):
        return value * info.context["scale"]


@pytest.mark.parametrize("many", [False, True])
async def test_pydantic_context_is_request_local_for_validation_and_output(many):
    serializer_class = adapt(ContextModel)

    async def validate(offset):
        payload = [{"value": 1}] if many else {"value": 1}
        serializer = serializer_class(
            data=payload, many=many, context={"offset": offset, "scale": 10}
        )
        assert await serializer.ais_valid(), serializer.errors
        expected = {"value": (1 + offset) * 10}
        assert await serializer.adata() == ([expected] if many else expected)

    await asyncio.gather(*(validate(offset) for offset in range(8)))


@pytest.mark.parametrize("many", [False, True])
def test_pydantic_context_reaches_output_validation_and_serialization(many):
    payload = [{"value": 1}] if many else {"value": 1}
    serializer = adapt(ContextModel)(
        payload, many=many, context={"offset": 2, "scale": 10}
    )
    assert serializer.data == ([{"value": 30}] if many else {"value": 30})


class ArrayStruct(msgspec.Struct, array_like=True):
    value: int


class ScalarModel(pydantic.BaseModel):
    value: int

    @pydantic.model_serializer
    def scalar(self) -> int:
        return self.value


@pytest.mark.parametrize(
    ("schema", "payload", "expected"),
    [
        (ArrayStruct, [3], [3]),
        (pydantic.RootModel[list[int]], [1, 2], [1, 2]),
        (pydantic.RootModel[int], 3, 3),
        (pydantic.RootModel[str], "value", "value"),
        (pydantic.RootModel[bool], False, False),
        (ScalarModel, {"value": 3}, 3),
    ],
)
@pytest.mark.parametrize("many", [False, True])
async def test_native_output_preserves_the_schema_shape(
    schema, payload, expected, many
):
    serializer_class = adapt(schema)
    serializer = serializer_class(data=[payload] if many else payload, many=many)
    assert await serializer.ais_valid(), serializer.errors
    result = await serializer.adata()
    assert result == ([expected] if many else expected)
    assert serializer.data == result


def test_root_models_require_an_explicit_partial_contract():
    serializer = adapt(pydantic.RootModel[list[int]])(data=[1], partial=True)
    with pytest.raises(ImproperlyConfigured, match="partial_schema"):
        serializer.is_valid()


@pytest.mark.parametrize("many", [False, True])
@pytest.mark.parametrize("partial", [False, True])
def test_pydantic_allowed_extra_values_are_not_lost(many, partial):
    class OpenModel(pydantic.BaseModel):
        model_config = pydantic.ConfigDict(extra="allow")
        value: int

    payload = {"value": 1, "extra": "retained"}
    serializer = adapt(OpenModel)(
        data=[payload] if many else payload, many=many, partial=partial
    )
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data == ([payload] if many else payload)
    assert serializer.data == ([payload] if many else payload)


def test_msgspec_values_do_not_reinspect_field_types(monkeypatch):
    class Nested(msgspec.Struct):
        value: int

    class Payload(msgspec.Struct, rename="camel"):
        nested_value: Nested

    obj = Payload(Nested(1))
    backend = adapt(Payload)().backend
    monkeypatch.setattr(
        msgspec.structs, "fields", lambda *args: pytest.fail("field type inspection")
    )
    assert backend.values(obj, partial=False) == {"nested_value": obj.nested_value}


def test_msgspec_field_metadata_is_reused_without_sharing_drf_fields(monkeypatch):
    class Payload(msgspec.Struct):
        value: int

    serializer_class = adapt(Payload)
    first = serializer_class().fields
    monkeypatch.setattr(
        msgspec.inspect, "type_info", lambda *args: pytest.fail("schema rebuilt")
    )
    second = serializer_class().fields
    assert first["value"] is not second["value"]


@pytest.mark.parametrize("backend", ["msgspec", "pydantic"])
def test_partial_many_output_contains_only_given_fields(backend):
    if backend == "msgspec":

        class Payload(msgspec.Struct):
            value: int
            other: str

    else:

        class Payload(pydantic.BaseModel):
            value: int
            other: str

    serializer = adapt(Payload)(data=[{"value": 1}, {}], partial=True, many=True)
    assert serializer.is_valid(), serializer.errors
    assert serializer.data == [{"value": 1}, {}]


def test_cached_pydantic_list_adapter_does_not_retain_request_context():
    class Context:
        pass

    context = Context()
    reference = weakref.ref(context)
    serializer = adapt(ContextModel)(
        [{"value": 1}],
        many=True,
        context={"offset": 1, "scale": 10, "request": context},
    )
    assert serializer.data == [{"value": 20}]
    del serializer, context
    gc.collect()
    assert reference() is None


@pytest.mark.parametrize("backend", ["msgspec", "pydantic"])
def test_warm_native_validation_does_not_build_introspection_fields(
    backend, monkeypatch
):
    if backend == "msgspec":

        class Payload(msgspec.Struct):
            value: int

    else:

        class Payload(pydantic.BaseModel):
            value: int

    serializer_class = adapt(Payload)
    warm = serializer_class(data={"value": 1})
    assert warm.is_valid()
    serializer = serializer_class(data={"value": 2})
    monkeypatch.setattr(
        type(warm.backend),
        "field_specs",
        lambda *args: pytest.fail("unused introspection fields constructed"),
    )
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data == {"value": 2}


def test_materialized_native_read_only_defaults_reach_drf_validators():
    class Payload(pydantic.BaseModel):
        value: int

    seen = []
    serializer = adapt(Payload)(data={"value": 1}, validators=[seen.append])
    serializer.fields["metadata"] = fields.ReadOnlyField(default="present")
    assert serializer.is_valid(), serializer.errors
    assert seen == [{"value": 1, "metadata": "present"}]


@pytest.mark.parametrize("lookup", ["property", "getattribute"])
def test_custom_native_fields_keep_read_only_defaults(lookup):
    class Payload(pydantic.BaseModel):
        value: int

    base = adapt(Payload)

    class PropertyFields(base):
        @property
        def fields(self):
            result = super().fields
            result["metadata"] = fields.ReadOnlyField(default="custom")
            return result

    class AttributeFields(base):
        def __getattribute__(self, name):
            result = super().__getattribute__(name)
            if name == "fields":
                result["metadata"] = fields.ReadOnlyField(default="custom")
            return result

    serializer_class = PropertyFields if lookup == "property" else AttributeFields
    for _ in range(2):
        seen = []
        serializer = serializer_class(data={"value": 1}, validators=[seen.append])
        assert serializer.is_valid(), serializer.errors
        assert seen == [{"value": 1, "metadata": "custom"}]


class Reference:
    def __init__(self, value):
        self.value = value


class CustomStruct(msgspec.Struct):
    reference: Reference


def decode_reference(target, value):
    if target is Reference and isinstance(value, str):
        return Reference(value)
    raise ValueError("Expected a reference string")


def encode_reference(value):
    if isinstance(value, Reference):
        return value.value
    raise NotImplementedError(type(value).__name__)


class CustomSerializer(MsgspecSerializer):
    class Meta:
        schema = CustomStruct
        dec_hook = decode_reference
        enc_hook = encode_reference
        schema_hook = staticmethod(lambda target: {"type": "string"})


@pytest.mark.parametrize("many", [False, True])
async def test_msgspec_custom_types_round_trip_and_generate_schema(many):
    payload = {"reference": "book:1"}
    serializer = CustomSerializer(data=[payload] if many else payload, many=many)
    assert await serializer.ais_valid(), serializer.errors
    assert await serializer.adata() == ([payload] if many else payload)
    backend = CustomSerializer().backend
    body, _ = backend.json_schema(CustomStruct, ref_prefix="#/", direction="request")
    assert body["properties"]["reference"]["type"] == "string"


def test_msgspec_custom_type_errors_remain_drf_errors():
    serializer = CustomSerializer(data={"reference": 1})
    assert not serializer.is_valid()
    assert serializer.errors["reference"][0].code == "invalid"


@pytest.mark.parametrize("backend", ["pydantic", "msgspec"])
async def test_native_user_validation_does_not_block_the_loop(backend):
    threads = []
    if backend == "pydantic":

        class Payload(pydantic.BaseModel):
            value: int

            @pydantic.model_validator(mode="after")
            def checked(self):
                threads.append(threading.get_ident())
                return self

    else:

        class Payload(msgspec.Struct):
            value: int

            def __post_init__(self):
                threads.append(threading.get_ident())

    serializer = adapt(Payload)(data={"value": 1})
    assert await serializer.ais_valid()
    assert threads
    assert threading.get_ident() not in threads


def test_msgspec_nested_hooks_do_not_disclose_subclass_fields():
    class Public(msgspec.Struct):
        reference: Reference

    class Internal(Public):
        secret: str

    class Envelope(msgspec.Struct):
        item: Public

    class Output(CustomSerializer):
        class Meta(CustomSerializer.Meta):
            schema = Envelope

    assert Output(Envelope(Internal(Reference("book:1"), "hidden"))).data == {
        "item": {"reference": "book:1"}
    }
