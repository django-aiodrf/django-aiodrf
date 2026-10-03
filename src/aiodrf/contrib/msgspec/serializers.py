"""
Serializers validated and represented by a msgspec ``Struct``: django-fastdrf's
(:mod:`fastdrf.msgspec.serializers`), which maintains them, on aiodrf's
asynchronous serializer bases.
"""

from typing import Any

from django.db.models import Model
from fastdrf.msgspec import serializers as _fastdrf

from aiodrf.contrib.typed import SchemaSerializer

__all__ = ["MsgspecSerializer", "serializer_for"]


class MsgspecSerializer(SchemaSerializer, _fastdrf.MsgspecSerializer):
    """
    django-fastdrf's ``MsgspecSerializer`` (validated and represented by a msgspec ``Struct``),
    on aiodrf's asynchronous serializer bases.
    """


def serializer_for(
    schema: object,
    output_schema: type | None = None,
    model: type[Model] | None = None,
) -> Any:
    """A ``MsgspecSerializer`` for a Struct (or an input and an output Struct)."""
    return _fastdrf.serializer_for(schema, output_schema, model, base=MsgspecSerializer)
