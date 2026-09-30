"""JSON and server-sent event streams with producer-owned cleanup."""

import asyncio

from django.urls import path
from drf_spectacular.utils import extend_schema

from aiodrf import serializers
from aiodrf.contrib.spectacular import StreamSchema
from aiodrf.response import (
    EventStreamResponse,
    ServerSentEvent,
    StreamingArrayResponse,
    StreamingResponse,
)
from aiodrf.views import APIView


class Item(serializers.Serializer):
    number = serializers.IntegerField()


async def rows():
    for number in range(3):
        await asyncio.sleep(0.01)
        yield {"number": number}


class Lines(APIView):
    @extend_schema(responses=StreamSchema(Item))
    async def get(self, request):
        return StreamingResponse(rows(), chunk_size=2)


class Array(APIView):
    async def get(self, request):
        return StreamingArrayResponse(rows())


class Events(APIView):
    async def get(self, request):
        async def events():
            async for row in rows():
                yield ServerSentEvent(row, event="number", id=str(row["number"]))

        return EventStreamResponse(events(), keepalive=1)


urlpatterns = [
    path("ndjson/", Lines.as_view()),
    path("array/", Array.as_view()),
    path("events/", Events.as_view()),
]
