"""Explicit schemas retain vendor datetime semantics without DRF coercion."""

import datetime

import msgspec
import pydantic
import pytest

from aiodrf.contrib.msgspec import MsgspecSerializer
from aiodrf.contrib.pydantic import PydanticSerializer


class StructStamp(msgspec.Struct):
    when: datetime.datetime


class ModelStamp(pydantic.BaseModel):
    when: datetime.datetime


@pytest.fixture(
    params=[(MsgspecSerializer, StructStamp), (PydanticSerializer, ModelStamp)]
)
def schema_serializer(request):
    base, schema = request.param
    return type("Stamp", (base,), {"Meta": type("Meta", (), {"schema": schema})})


@pytest.mark.parametrize(
    "value",
    [
        datetime.datetime(2026, 1, 2, 3, 4, 5),
        datetime.datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=datetime.UTC),
        datetime.datetime(
            2026,
            1,
            2,
            3,
            4,
            5,
            tzinfo=datetime.timezone(datetime.timedelta(hours=5, minutes=45)),
        ),
        datetime.datetime.min,
        datetime.datetime.max.replace(tzinfo=datetime.UTC),
    ],
)
async def test_python_datetime_validation_and_output_match_vendor(
    schema_serializer, value
):
    schema = schema_serializer.Meta.schema
    payload = {"when": value}
    if issubclass(schema, msgspec.Struct):
        native = msgspec.convert(payload, schema, strict=True)
        expected = msgspec.to_builtins(native)
    else:
        native = schema.model_validate(payload, strict=True)
        expected = native.model_dump(mode="json", by_alias=True)
    serializer = schema_serializer(data=payload)
    assert await serializer.ais_valid(), serializer.errors
    assert serializer.validated_data["when"] == native.when
    assert await schema_serializer(native).adata() == expected
    assert await schema_serializer([native], many=True).adata() == [expected]


@pytest.mark.parametrize("value", ["not-a-date", "2026-02-30T12:00:00Z"])
async def test_invalid_datetimes_report_field_errors(schema_serializer, value):
    serializer = schema_serializer(data={"when": value})
    assert not await serializer.ais_valid()
    assert "when" in serializer.errors


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("value", ["2026-01-02T03:04:05Z", "2026-01-02T03:04:05+05:45"])
async def test_iso_input_retains_explicit_vendor_strictness(
    schema_serializer, strict, value
):
    schema_serializer.Meta.strict = strict
    schema = schema_serializer.Meta.schema
    payload = {"when": value}
    try:
        native = (
            msgspec.convert(payload, schema, strict=strict)
            if issubclass(schema, msgspec.Struct)
            else schema.model_validate(payload, strict=strict or None)
        )
    except (msgspec.ValidationError, pydantic.ValidationError):
        native = None
    serializer = schema_serializer(data=payload)
    assert await serializer.ais_valid() is (native is not None)
    if native is not None:
        assert serializer.validated_data["when"] == native.when
    else:
        assert "when" in serializer.errors
