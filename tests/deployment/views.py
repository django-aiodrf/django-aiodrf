import asyncio
import os
import time

from django.conf import settings
from django.db import connection
from django.http import JsonResponse
from django.middleware.csrf import get_token
from rest_framework import generics, serializers
from rest_framework.exceptions import ValidationError
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response as DRFResponse
from rest_framework.views import APIView as DRFAPIView

from aiodrf.asgi import get_lifespan_state
from aiodrf.generics import ListAPIView, RetrieveAPIView
from aiodrf.response import EventStreamResponse, Response
from aiodrf.utils import run_sync
from aiodrf.views import APIView
from tests.deployment.lifecycle import Resources
from tests.testapp.models import Author


class AuthorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Author
        fields = ["id", "name"]


class Authors(ListAPIView):
    queryset = Author.objects.order_by("pk")
    serializer_class = AuthorSerializer


class DRFAuthors(generics.ListAPIView):
    queryset = Author.objects.order_by("pk")
    serializer_class = AuthorSerializer


class AuthorDetail(RetrieveAPIView):
    queryset = Author.objects.order_by("pk")
    serializer_class = AuthorSerializer


class DRFAuthorDetail(generics.RetrieveAPIView):
    queryset = Author.objects.order_by("pk")
    serializer_class = AuthorSerializer


def read_authors(pk=None):
    """One sync ORM/representation batch, matching the native view's payload."""
    objects = (
        Author.objects.get(pk=pk)
        if pk is not None
        else list(Author.objects.order_by("pk"))
    )
    return AuthorSerializer(objects, many=pk is None).data


class DRFRead(DRFAPIView):
    def get(self, request, pk=None):
        return DRFResponse(read_authors(pk))


class AsyncRead(APIView):
    async def get(self, request, pk=None):
        return Response(await run_sync(read_authors)(pk))


class NativeRead(APIView):
    """Benchmark-only adapter: no lazy relations, validation or generic hooks."""

    async def get(self, request, pk=None):
        objects = (
            await Author.async_objects.aget(pk=pk)
            if pk is not None
            else [author async for author in Author.async_objects.order_by("pk")]
        )
        # Only loaded id/name fields; this serializer cannot issue lazy SQL.
        return Response(AuthorSerializer(objects, many=pk is None).data)


async def external_body(resources):
    if resources.aio_client is not None:
        async with resources.aio_client.get(resources.upstream) as response:
            response.raise_for_status()
            return await response.text()
    response = await resources.client.get(resources.upstream)
    response.raise_for_status()
    return response.text


class External(APIView):
    async def get(self, request):
        resources = get_lifespan_state(request, Resources)
        return Response({"body": await external_body(resources)})


class DRFExternal(DRFAPIView):
    def get(self, request):
        resources = get_lifespan_state(request, Resources)
        response = resources.sync_client.get(resources.upstream)
        response.raise_for_status()
        return DRFResponse({"body": response.text})


async def django_external(request):
    """Async Django control, using the same client and middleware as the API views."""
    resources = get_lifespan_state(request, Resources)
    return JsonResponse(
        {"body": await external_body(resources)},
        json_dumps_params={"separators": (",", ":")},
    )


def nested_payload():
    return [
        {"id": index, "name": f"item-{index}", "tags": [1, 2, 3], "author": {"id": 1}}
        for index in range(100)
    ]


class NestedJSON(APIView):
    async def get(self, request):
        return Response(nested_payload())


class DRFNestedJSON(DRFAPIView):
    def get(self, request):
        return DRFResponse(nested_payload())


class Stream(APIView):
    response_class = EventStreamResponse

    async def get(self, request):
        resources = get_lifespan_state(request, Resources)
        try:
            interval = float(request.query_params.get("interval", "0.05"))
            padding = int(request.query_params.get("padding", "0"))
            limit = int(request.query_params.get("limit", "0"))
        except ValueError:
            raise ValidationError("Invalid stream parameters.") from None
        if (
            not 0.005 <= interval <= 5
            or not 0 <= padding <= 65536
            or not 0 <= limit <= 1000
        ):
            raise ValidationError("Stream parameters outside the test bounds.")
        buffering = request.query_params.get("buffering")
        if buffering not in (None, "yes", "no"):
            raise ValidationError("buffering must be yes or no.")

        async def events():
            resources.opened += 1
            try:
                yield {"ready": True, "pid": os.getpid()}
                sequence = 0
                while not limit or sequence < limit:
                    await asyncio.sleep(interval)
                    yield {"sequence": sequence, "padding": "x" * padding}
                    sequence += 1
            finally:
                resources.closed += 1

        headers = {} if buffering is None else {"X-Accel-Buffering": buffering}
        return self.response_class(events(), headers=headers)


def pool_stats():
    return connection.pool.get_stats()


class Metrics(APIView):
    async def post(self, request):
        resources = get_lifespan_state(request, Resources)
        resources.lag_epoch += 1
        resources.lag_ms.clear()
        return Response({"reset": True})

    async def get(self, request):
        resources = get_lifespan_state(request, Resources)
        native_pool = None
        if settings.CONFIG.get("db_backend") == "async":
            from django_async_backend.db import async_connections

            native_pool = async_connections["default"].pool.get_stats()
        return Response(
            {
                "pid": os.getpid(),
                "opened": resources.opened,
                "closed": resources.closed,
                "lag_ms": list(resources.lag_ms),
                "cpu_seconds": time.process_time(),
                "csrf": get_token(request._request),
                "user": (await request.auser()).username,
                "pool": await run_sync(pool_stats)(),
                "native_pool": native_pool,
            }
        )


class Payload(APIView):
    async def post(self, request):
        data = await request.adata()
        return Response(
            {"size": data["file"].size if "file" in data else len(data["text"])}
        )


class LargeJSON(APIView):
    async def get(self, request):
        try:
            size = int(request.query_params.get("bytes", "1048576"))
        except ValueError:
            raise ValidationError("Invalid byte count.") from None
        if not 1 <= size <= 8 * 1024 * 1024:
            raise ValidationError("Byte count outside the test bounds.")
        return Response({"text": "x" * size})


class ThreadedJSONRenderer(JSONRenderer):
    """An explicit unregistered override uses Django's worker render path."""

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return super().render(data, accepted_media_type, renderer_context)


class ThreadedLargeJSON(LargeJSON):
    renderer_classes = [ThreadedJSONRenderer]


class FastLargeJSON(LargeJSON):
    from aiodrf.contrib.msgspec.renderers import MsgspecJSONRenderer

    renderer_classes = [MsgspecJSONRenderer]
