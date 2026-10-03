"""A schema serializer's ``.data`` after ``validate()`` changed the input."""

import msgspec
import pydantic
import pytest

from aiodrf import aio
from aiodrf.contrib.msgspec.serializers import MsgspecSerializer
from aiodrf.contrib.pydantic.serializers import PydanticSerializer


class Title(msgspec.Struct):
    title: str


class TitleModel(pydantic.BaseModel):
    title: str


def upper(base, schema):
    class Upper(base):
        class Meta:
            pass

        def validate(self, attrs):
            return {**attrs, "title": attrs["title"].upper()}

    Upper.Meta.schema = schema
    return Upper


@pytest.mark.parametrize(
    ("base", "schema"), [(MsgspecSerializer, Title), (PydanticSerializer, TitleModel)]
)
async def test_data_is_what_validate_returned(base, schema):
    # DRF represents validated_data, not the input read before validate().
    serializer = upper(base, schema)(data={"title": "hello"})
    assert await aio.is_valid(serializer)
    assert await aio.data(serializer) == {"title": "HELLO"}
