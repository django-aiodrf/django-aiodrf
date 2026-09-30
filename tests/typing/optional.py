"""Installed-wheel consumers with optional schema backends present."""

from typing import assert_type

import msgspec
from pydantic import BaseModel

from aiodrf.contrib.msgspec import MsgspecSerializer
from aiodrf.contrib.pydantic import PydanticSerializer


class Struct(msgspec.Struct):
    value: int


class Schema(BaseModel):
    value: int


class StructInput(MsgspecSerializer):
    class Meta:
        schema = Struct


class ModelInput(PydanticSerializer):
    class Meta:
        schema = Schema


async def validate() -> None:
    assert_type(await StructInput(data={"value": 1}).ais_valid(), bool)
    assert_type(await ModelInput(data={"value": 1}).ais_valid(), bool)
