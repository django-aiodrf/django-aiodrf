"""Consumer annotations when the spectacular extra is installed."""

from typing import assert_type

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers

from aiodrf.contrib.spectacular import StreamSchema


class Item(serializers.Serializer):
    value = serializers.IntegerField()


assert_type(StreamSchema(Item), StreamSchema)
assert_type(StreamSchema(Item()), StreamSchema)
extend_schema(
    responses={
        (200, "text/event-stream"): OpenApiResponse(
            StreamSchema(Item), description="Events."
        ),
        (200, "application/x-ndjson"): StreamSchema(Item()),
    }
)
