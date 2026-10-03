"""
drf-spectacular extensions for aiodrf's authentication and stream schemas.

Schema serializers (``MsgspecSerializer``, ``PydanticSerializer``) are
documented by django-fastdrf's extension (``fastdrf.spectacular``), which
aiodrf's application installs.
"""

from typing import Any

from drf_spectacular.authentication import SessionScheme
from drf_spectacular.extensions import OpenApiSerializerExtension
from drf_spectacular.openapi import AutoSchema
from drf_spectacular.plumbing import (
    ComponentIdentity,
)
from drf_spectacular.utils import Direction

__all__ = ["AioDRFSessionScheme", "StreamSchemaExtension"]

COMPONENTS = "#/components/schemas/"


class AioDRFSessionScheme(SessionScheme):
    # Spectacular matches ``SessionScheme`` against DRF's class exactly.
    target_class = "aiodrf.authentication.SessionAuthentication"


class StreamSchemaExtension(OpenApiSerializerExtension):
    target_class = "aiodrf.contrib.spectacular.StreamSchema"

    def get_name(self, auto_schema: AutoSchema, direction: Direction) -> str:
        if direction != "response":
            raise ValueError(
                "StreamSchema is only supported in extend_schema(responses=...)."
            )
        item = auto_schema.resolve_serializer(self.target.item_serializer, "response")
        return f"{item.name if item else 'Empty'}Stream"

    def get_identity(
        self, auto_schema: AutoSchema, direction: Direction
    ) -> ComponentIdentity:
        item = auto_schema.resolve_serializer(self.target.item_serializer, "response")
        return ComponentIdentity((type(self), item.ref if item else None))

    def map_serializer(
        self, auto_schema: AutoSchema, direction: Direction
    ) -> dict[str, Any]:
        item = auto_schema.resolve_serializer(self.target.item_serializer, "response")
        return {
            "type": "string",
            "description": (
                "Item stream, not a JSON array. x-aiodrf-item-schema describes one NDJSON "
                "line or the decoded JSON in an SSE event's data field, excluding event "
                "metadata and comments."
            ),
            "x-aiodrf-item-schema": (
                item.ref if item else {"type": "object", "additionalProperties": False}
            ),
        }
