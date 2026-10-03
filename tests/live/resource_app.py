"""Disposable, process-isolated ASGI application for resource/load contracts.

Only the harness calls this module, with a new temporary SQLite path. It does
not import or modify a project's settings or an existing database.
"""

import asyncio
import contextlib
import json
import os
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Resources:
    client: object
    upstream: str
    opened: int = 0
    closed: int = 0
    lag_ms: deque = field(default_factory=lambda: deque(maxlen=10000))


async def upstream(reader, writer, *, delay=0.005):
    try:
        await reader.readuntil(b"\r\n\r\n")
        await asyncio.sleep(delay)
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok"
        )
        await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


async def monitor(resources):
    loop = asyncio.get_running_loop()
    while True:
        target = loop.time() + 0.01
        await asyncio.sleep(0.01)
        resources.lag_ms.append(max(0, loop.time() - target) * 1000)


def application(database, journal, *, initialize_database=True):
    import django
    import httpx
    from django.conf import settings

    settings.configure(
        SECRET_KEY="aiodrf-disposable-load-test-only",
        DEBUG=False,
        ALLOWED_HOSTS=["127.0.0.1"],
        ROOT_URLCONF=__name__,
        DATABASES={
            "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": database}
        },
        INSTALLED_APPS=[
            "django.contrib.auth",
            "django.contrib.contenttypes",
            "django.contrib.sessions",
            "rest_framework",
            "aiodrf",
            "tests.testapp",
        ],
        MIDDLEWARE=[
            "django.middleware.security.SecurityMiddleware",
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.middleware.common.CommonMiddleware",
            "django.middleware.csrf.CsrfViewMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
            "django.contrib.messages.middleware.MessageMiddleware",
            "django.middleware.clickjacking.XFrameOptionsMiddleware",
        ],
        SESSION_ENGINE="django.contrib.sessions.backends.signed_cookies",
        FILE_UPLOAD_MAX_MEMORY_SIZE=65536,
        DEFAULT_AUTO_FIELD="django.db.models.AutoField",
    )
    django.setup()
    from aiodrf_asgi_lifespan.asgi import get_lifespan_state
    from django.db import connection
    from django.urls import path
    from rest_framework import generics as drf_generics
    from rest_framework import serializers

    from aiodrf.asgi import get_asgi_application
    from aiodrf.generics import ListAPIView
    from aiodrf.response import EventStreamResponse, Response
    from aiodrf.utils import run_sync
    from aiodrf.views import APIView
    from tests.testapp.models import Author

    # Only the one test-owned table needed by this read workload. The session
    # backend uses signed cookies, and requests are anonymous SessionAuthentication.
    if initialize_database:
        with connection.schema_editor() as editor:
            editor.create_model(Author)
        Author.objects.bulk_create(
            [Author(name=f"author-{index}") for index in range(30)]
        )
        connection.close()

    @contextlib.asynccontextmanager
    async def lifespan():
        async with await asyncio.start_server(upstream, "127.0.0.1", 0) as server:
            address = server.sockets[0].getsockname()
            async with httpx.AsyncClient(timeout=5) as client:
                resources = Resources(client, f"http://127.0.0.1:{address[1]}/")
                background = asyncio.create_task(monitor(resources))
                try:
                    yield resources
                finally:
                    background.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await background
            # A file per worker proves actual cleanup after SIGTERM, not only
            # a response from an application that is still running.
            record = {
                "pid": os.getpid(),
                "opened": resources.opened,
                "closed": resources.closed,
                "background_done": background.done(),
                "client_closed": client.is_closed,
            }
            await run_sync(Path(journal).write_text)(
                json.dumps(record), encoding="utf-8"
            )

    class AuthorSerializer(serializers.ModelSerializer):
        class Meta:
            model = Author
            fields = ["id", "name"]

    class Authors(ListAPIView):
        queryset = Author.objects.order_by("pk")
        serializer_class = AuthorSerializer

    class DRFAuthors(drf_generics.ListAPIView):
        queryset = Author.objects.order_by("pk")
        serializer_class = AuthorSerializer

    class External(APIView):
        async def get(self, request):
            resources = get_lifespan_state(request, Resources)
            response = await resources.client.get(resources.upstream)
            response.raise_for_status()
            return Response({"body": response.text})

    class Stream(APIView):
        async def get(self, request):
            resources = get_lifespan_state(request, Resources)

            async def events():
                resources.opened += 1
                try:
                    yield {"ready": True}
                    while True:
                        await asyncio.sleep(0.05)
                        yield {"tick": True}
                finally:
                    resources.closed += 1

            return EventStreamResponse(events())

    class Metrics(APIView):
        async def get(self, request):
            resources = get_lifespan_state(request, Resources)
            return Response(
                {
                    "pid": os.getpid(),
                    "opened": resources.opened,
                    "closed": resources.closed,
                    "lag_ms": list(resources.lag_ms),
                    "cpu_seconds": time.process_time(),
                }
            )

    class Payload(APIView):
        async def post(self, request):
            data = await request.adata()
            return Response(
                {"size": data["file"].size if "file" in data else len(data["text"])}
            )

    # Pass a URLConf object, not a module-global mutable route registry.
    class URLs:
        urlpatterns = [
            path("drf/", DRFAuthors.as_view()),
            path("aiodrf/", Authors.as_view()),
            path("external/", External.as_view()),
            path("stream/", Stream.as_view()),
            path("metrics/", Metrics.as_view()),
            path("payload/", Payload.as_view()),
        ]

    settings.ROOT_URLCONF = URLs
    return get_asgi_application(lifespan=lifespan)


def serve(sock, database, journal):
    import uvicorn

    app = application(database, journal)
    config = uvicorn.Config(
        app,
        lifespan="on",
        log_level="critical",
        access_log=False,
        timeout_graceful_shutdown=2,
    )
    uvicorn.Server(config).run(sockets=[sock])
