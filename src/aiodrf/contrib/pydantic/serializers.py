"""
Serializers validated and represented by a pydantic ``BaseModel``:
django-fastdrf's (:mod:`fastdrf.pydantic.serializers`), which maintains them,
on aiodrf's asynchronous serializer bases.
"""

from typing import Any

from django.db.models import Model
from fastdrf.pydantic import serializers as _fastdrf

from aiodrf.contrib.typed import SchemaSerializer

__all__ = ["PydanticSerializer", "serializer_for"]


class PydanticSerializer(SchemaSerializer, _fastdrf.PydanticSerializer):
    """
    django-fastdrf's ``PydanticSerializer`` (validated and represented by a pydantic model),
    on aiodrf's asynchronous serializer bases.
    """


def serializer_for(
    schema: object,
    output_schema: type | None = None,
    model: type[Model] | None = None,
) -> Any:
    """A ``PydanticSerializer`` for a model (or an input and an output model)."""
    return _fastdrf.serializer_for(
        schema, output_schema, model, base=PydanticSerializer
    )
