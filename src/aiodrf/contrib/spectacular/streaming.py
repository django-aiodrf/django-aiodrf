"""Schema-only annotations for item streams; no runtime serialization."""

from typing import Any, NoReturn

from drf_spectacular.utils import extend_schema_serializer
from rest_framework.serializers import Serializer

__all__ = ["StreamSchema"]


@extend_schema_serializer(many=False)
class StreamSchema(Serializer):
    """
    Annotate an SSE or NDJSON response with the serializer of one JSON item.

    Use with drf-spectacular's explicit status/media-type keys::

        @extend_schema(responses={
            (200, "text/event-stream"): StreamSchema(ItemSerializer),
        })

    The response is a string, not a JSON array. ``x-aiodrf-item-schema``
    references the schema of one NDJSON line or the decoded JSON in an SSE
    event's ``data`` field. This extension is documentation, not a standard
    OpenAPI streaming contract or runtime validation. SSE comments and event
    metadata are not items. Use a plain string schema for non-JSON SSE data.

    Accepts a DRF/aiodrf serializer class or instance, including the optional
    msgspec/pydantic serializer adapters. Do not pass ``many=True``: describe
    one item. This annotation cannot be used as a runtime serializer.
    """

    def __init__(self, item_serializer: Serializer | type[Serializer]) -> None:
        if not (
            isinstance(item_serializer, Serializer)
            or (
                isinstance(item_serializer, type)
                and issubclass(item_serializer, Serializer)
            )
        ):
            raise TypeError(
                "StreamSchema requires a serializer class or instance, without many=True."
            )
        self.item_serializer = item_serializer
        super().__init__()

    @property
    def data(self) -> NoReturn:
        raise TypeError(
            "StreamSchema is an annotation for extend_schema(responses=...), not a serializer."
        )

    def to_internal_value(self, data: Any) -> NoReturn:
        raise TypeError(
            "StreamSchema is an annotation for extend_schema(responses=...), not a serializer."
        )

    def to_representation(self, instance: Any) -> NoReturn:
        raise TypeError(
            "StreamSchema is an annotation for extend_schema(responses=...), not a serializer."
        )
