"""
``partial=True`` keeps the schemas' contracts: the output schema's
serialization for ``.data`` before a save, the input schema's Struct
configuration for validation.
"""

import msgspec
import pydantic
import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework.renderers import JSONRenderer

from aiodrf.contrib.msgspec import MsgspecSerializer
from aiodrf.contrib.typed import schema_serializer

SECRET = "private-token"


def rendered(serializer):
    data = serializer.data
    body = JSONRenderer().render(data)
    assert SECRET.encode() not in body
    return data


# -- pydantic: the output schema's serializers, aliases and exclusions ----------------


class Redacted(pydantic.BaseModel):
    secret: str
    note: str | None = None

    @pydantic.field_serializer("secret")
    def hide(self, value):
        return "[redacted]"


class WholeRedacted(pydantic.BaseModel):
    secret: str

    @pydantic.model_serializer
    def hide(self):
        return {"secret": "[redacted]"}


class Account(pydantic.BaseModel):
    name: str
    secret: str
    token: str = ""


class PublicAccount(pydantic.BaseModel):
    name: str = pydantic.Field(serialization_alias="displayName")
    secret: str | None = None
    token: str = pydantic.Field("", exclude=True)

    @pydantic.field_serializer("secret")
    def hide(self, value):
        return None if value is None else "[redacted]"

    @pydantic.computed_field
    @property
    def initial(self) -> str:
        return self.name[0]


def serializer_class(schema, output_schema=None):
    return schema_serializer(schema, output_schema or schema)


@pytest.mark.parametrize("partial", [False, True])
def test_partial_data_uses_the_field_serializers(partial):
    serializer = serializer_class(Redacted)(data={"secret": SECRET}, partial=partial)
    assert serializer.is_valid(), serializer.errors
    expected = (
        {"secret": "[redacted]"} if partial else {"secret": "[redacted]", "note": None}
    )
    assert rendered(serializer) == expected


@pytest.mark.parametrize("partial", [False, True])
def test_partial_data_keeps_null_and_leaves_out_what_was_not_given(partial):
    serializer = serializer_class(Redacted)(data={"note": None}, partial=partial)
    assert serializer.is_valid() is partial
    if partial:
        assert rendered(serializer) == {"note": None}


def test_a_model_serializer_cannot_represent_partial_input():
    serializer = serializer_class(WholeRedacted)(data={"secret": SECRET})
    assert serializer.is_valid(), serializer.errors
    assert rendered(serializer) == {"secret": "[redacted]"}

    serializer = serializer_class(WholeRedacted)(data={"secret": SECRET}, partial=True)
    assert serializer.is_valid(), serializer.errors
    with pytest.raises(TypeError, match="model_serializer"):
        serializer.data  # noqa: B018


@pytest.mark.parametrize("partial", [False, True])
def test_partial_data_is_the_output_schemas(partial):
    body = {"name": "Ada", "secret": SECRET, "token": SECRET}
    serializer = serializer_class(Account, PublicAccount)(data=body, partial=partial)
    assert serializer.is_valid(), serializer.errors
    expected = {"displayName": "Ada", "secret": "[redacted]"}
    # The given fields only: a computed field could read one left out.
    assert rendered(serializer) == (
        expected if partial else {**expected, "initial": "A"}
    )


def test_partial_data_leaves_out_the_fields_not_given():
    serializer = serializer_class(Account, PublicAccount)(
        data={"secret": SECRET}, partial=True
    )
    assert serializer.is_valid(), serializer.errors
    assert rendered(serializer) == {"secret": "[redacted]"}
    serializer = serializer_class(Account, PublicAccount)(data={}, partial=True)
    assert serializer.is_valid(), serializer.errors
    assert rendered(serializer) == {}


# -- msgspec: the output Struct's names and nested fields ----------------------------


class Owner(msgspec.Struct):
    name: str
    password: str


class PublicOwner(msgspec.Struct):
    name: str


class Document(msgspec.Struct):
    title: str
    owner: Owner


class PublicDocument(msgspec.Struct, rename="camel", tag=True):
    title: str
    owner: PublicOwner
    page_count: int = 0


@pytest.mark.parametrize("partial", [False, True])
def test_partial_msgspec_data_is_the_output_schemas(partial):
    body = {"title": "Notes", "owner": {"name": "Ada", "password": SECRET}}
    serializer = serializer_class(Document, PublicDocument)(data=body, partial=partial)
    assert serializer.is_valid(), serializer.errors
    expected = {"type": "PublicDocument", "title": "Notes", "owner": {"name": "Ada"}}
    if not partial:
        expected["pageCount"] = 0
    assert rendered(serializer) == expected


# -- msgspec: a derived PATCH schema keeps the Struct's configuration ----------------


class Event(msgspec.Struct, tag="allowed"):
    value: int


class Numbered(
    msgspec.Struct, tag_field="kind", tag=3, rename="camel", forbid_unknown_fields=True
):
    page_count: int
    note: str = ""


class Named(msgspec.Struct, tag=True):
    value: int


class Row(msgspec.Struct, array_like=True):
    value: int
    note: str = ""


class RowPatch(msgspec.Struct, array_like=True):
    value: int | msgspec.UnsetType = msgspec.UNSET
    note: str | msgspec.UnsetType = msgspec.UNSET


class EventPatch(msgspec.Struct, tag="allowed", tag_field="type"):
    value: int | msgspec.UnsetType = msgspec.UNSET


def errors(schema, data, partial, partial_schema=None):
    attrs = {"schema": schema}
    if partial_schema is not None:
        attrs["partial_schema"] = partial_schema
    serializer_class = type(
        "Serializer", (MsgspecSerializer,), {"Meta": type("Meta", (), attrs)}
    )
    serializer = serializer_class(data=data, partial=partial)
    return None if serializer.is_valid() else serializer.errors


@pytest.mark.parametrize(
    ("schema", "valid", "invalid"),
    [
        (Event, {"type": "allowed", "value": 1}, [{"type": "wrong", "value": 1}]),
        (Numbered, {"kind": 3, "pageCount": 1}, [{"kind": 4}, {"kind": "3"}]),
        (Named, {"type": "Named", "value": 1}, [{"type": "Event", "value": 1}]),
    ],
)
def test_a_partial_update_checks_the_tag_as_a_full_one_does(schema, valid, invalid):
    # msgspec accepts a Struct's tag left out, and checks one that is given.
    untagged = {
        key: value for key, value in valid.items() if key not in ("type", "kind")
    }
    for data in (valid, untagged):
        assert errors(schema, data, False) is None
        assert errors(schema, data, True) is None
    for data in invalid:
        full = errors(schema, {**valid, **data}, False)
        assert full is not None
        assert errors(schema, data, True) == full


def test_an_explicit_partial_schema_checks_its_own_tag():
    assert errors(Event, {"type": "allowed"}, True, EventPatch) is None
    assert errors(Event, {"type": "wrong"}, True, EventPatch) is not None


def test_a_derived_partial_update_keeps_names_and_unknown_field_policy():
    assert errors(Numbered, {"kind": 3, "note": "x"}, True) is None
    assert errors(Numbered, {"kind": 3, "page_count": 1}, True) is not None
    serializer_class = type(
        "Serializer",
        (MsgspecSerializer,),
        {"Meta": type("Meta", (), {"schema": Numbered})},
    )
    serializer = serializer_class(data={"kind": 3, "note": "x"}, partial=True)
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data == {"note": "x"}
    assert serializer.data == {"kind": 3, "note": "x"}


def test_an_array_like_struct_needs_an_explicit_partial_schema():
    # A shorter array cannot say which of its fields it leaves out.
    with pytest.raises(ImproperlyConfigured, match="partial_schema"):
        errors(Row, [1], True)
    assert errors(Row, [1], True, RowPatch) is None
    assert errors(Row, {"value": 1}, True, RowPatch) is not None
