"""Typed serializers preserve backend constraints and form collection aliases."""

from typing import Annotated

import msgspec
import pydantic
import pytest
from django.http import QueryDict

from aiodrf.contrib.typed import adapt


@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize("key", ["tags", "labels"])
def test_pydantic_form_alias_choices_preserve_repeated_values(key, partial):
    class Payload(pydantic.BaseModel):
        tags: list[str] = pydantic.Field(
            validation_alias=pydantic.AliasChoices("tags", "labels")
        )

    serializer = adapt(Payload)(data=QueryDict(f"{key}=one&{key}=two"), partial=partial)
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data == {"tags": ["one", "two"]}


@pytest.mark.parametrize("partial", [False, True])
def test_pydantic_form_alias_path_does_not_raise_type_error(partial):
    class Payload(pydantic.BaseModel):
        tags: list[str] = pydantic.Field(
            validation_alias=pydantic.AliasPath("body", "tags")
        )

    # Flat form input cannot express this nested path. This must be a normal
    # validation error, not an unhashable AliasPath escaping as HTTP 500.
    serializer = adapt(Payload)(data=QueryDict("body=one&body=two"), partial=partial)
    if partial:
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data == {}
    else:
        assert not serializer.is_valid()
        assert serializer.errors["body"]["tags"][0].code == "required"


def test_a_missing_alias_path_is_reported_where_the_input_stops():
    class Payload(pydantic.BaseModel):
        value: int = pydantic.Field(
            validation_alias=pydantic.AliasPath("payload", "value")
        )

    serializer = adapt(Payload)(data={})
    assert not serializer.is_valid()
    assert list(serializer.errors) == ["payload"]
    assert serializer.errors["payload"][0].code == "required"
    serializer = adapt(Payload)(data={"payload": {}})
    assert not serializer.is_valid()
    assert serializer.errors["payload"]["value"][0].code == "required"


@pytest.mark.parametrize("partial", [False, True])
def test_pydantic_form_populate_by_name_preserves_collection(partial):
    class Payload(pydantic.BaseModel):
        model_config = pydantic.ConfigDict(populate_by_name=True)
        tags: list[str] = pydantic.Field(alias="labels")

    serializer = adapt(Payload)(data=QueryDict("tags=one&tags=two"), partial=partial)
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data == {"tags": ["one", "two"]}


@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize("choice", [False, True])
def test_pydantic_single_segment_alias_path_preserves_collection(partial, choice):
    alias = pydantic.AliasPath("labels")
    if choice:
        alias = pydantic.AliasChoices("other", alias)

    class Payload(pydantic.BaseModel):
        tags: list[str] = pydantic.Field(validation_alias=alias)

    serializer = adapt(Payload)(
        data=QueryDict("labels=one&labels=two"), partial=partial
    )
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data == {"tags": ["one", "two"]}


def test_pydantic_form_does_not_enable_population_by_name():
    class Payload(pydantic.BaseModel):
        tags: list[str] = pydantic.Field(alias="labels")

    serializer = adapt(Payload)(data=QueryDict("tags=one&tags=two"))
    assert not serializer.is_valid()
    assert serializer.errors["labels"][0].code == "required"


@pytest.mark.parametrize("partial", [False, True])
def test_msgspec_form_fixed_tuple_keeps_all_values(partial):
    class Payload(msgspec.Struct):
        coordinates: tuple[int, int]

    serializer = adapt(Payload)(
        data=QueryDict("coordinates=1&coordinates=2"), partial=partial
    )
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data == {"coordinates": (1, 2)}


@pytest.mark.parametrize("backend", ["msgspec", "pydantic"])
@pytest.mark.parametrize("partial", [False, True])
def test_nested_collection_constraints_survive_schema_adaptation(backend, partial):
    if backend == "msgspec":

        class Payload(msgspec.Struct):
            values: list[Annotated[str, msgspec.Meta(min_length=5)]] | None

    else:

        class Payload(pydantic.BaseModel):
            values: list[Annotated[str, pydantic.Field(min_length=5)]] | None

    serializer_class = adapt(Payload)
    invalid = serializer_class(data={"values": ["no"]}, partial=partial)
    assert not invalid.is_valid()
    assert invalid.errors["values"][0][0].code == (
        "invalid" if backend == "msgspec" else "string_too_short"
    )
    for value in (["valid"], None):
        valid = serializer_class(data={"values": value}, partial=partial)
        assert valid.is_valid(), valid.errors
        assert valid.validated_data == {"values": value}
