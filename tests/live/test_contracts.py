"""Real-server correctness gates and local measurements, using test databases only.

Run explicitly with pytest (or ``nox -s live``). AIODRF_BENCH_OUTPUT selects a
new JSON report path; AIODRF_BENCH_CPUS pins the entire local harness. The
client and server share a process, so RSS/CPU describe the harness, not a
standalone production server. No existing benchmark database is seeded.
"""

import asyncio
import hashlib
import json
import os
import platform
import socket
import statistics
import sys
import threading
import time
import tracemalloc
from contextlib import contextmanager
from importlib.metadata import version
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import uvicorn
from asgiref.sync import SyncToAsync, sync_to_async
from django.core.asgi import get_asgi_application
from django.db import connection
from django.db.backends.utils import CursorWrapper
from django.http import JsonResponse
from django.test import override_settings
from django.urls import path
from fastdrf import inputs
from rest_framework import serializers
from rest_framework import viewsets as drf_viewsets
from rest_framework.views import APIView as DRFAPIView

from aiodrf import aio, viewsets
from aiodrf.response import Response, StreamingResponse
from aiodrf.test import count_hops
from aiodrf.utils import run_sync
from aiodrf.views import APIView
from tests.testapp.models import Author, Book


def percentile(values, fraction):
    return sorted(values)[min(len(values) - 1, int(len(values) * fraction))]


def rss_bytes():
    statm = Path("/proc/self/statm")
    return (
        int(statm.read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")
        if statm.exists()
        else None
    )


def database_runtime():
    result = {"conn_max_age": connection.settings_dict["CONN_MAX_AGE"]}
    if connection.vendor == "postgresql":
        result.update(
            server=connection.pg_version,
            driver=connection.Database.__version__,
            implementation=connection.Database.pq.__impl__,
            libpq=connection.Database.pq.version(),
        )
    else:
        result["sqlite"] = connection.Database.sqlite_version
    return result


class AuthorSchema(serializers.ModelSerializer):
    class Meta:
        model = Author
        fields = ["id", "name"]


class BookSchema(serializers.ModelSerializer):
    author = AuthorSchema()

    class Meta:
        model = Book
        fields = ["id", "title", "pages", "author"]


class Policies:
    authentication_classes = []
    permission_classes = []
    pagination_class = None
    queryset = Book.objects.select_related("author").order_by("pk")
    serializer_class = BookSchema


class DRFBooks(Policies, drf_viewsets.ReadOnlyModelViewSet):
    pass


class Books(Policies, viewsets.ReadOnlyModelViewSet):
    pass


class CompiledBookSchema(BookSchema):
    class Meta(BookSchema.Meta):
        serializer_backend = "msgspec"


class PydanticBookSchema(BookSchema):
    class Meta(BookSchema.Meta):
        serializer_backend = "pydantic"


class Authors(Policies, viewsets.ModelViewSet):
    queryset = Author.objects.order_by("pk")
    serializer_class = AuthorSchema


class DRFAuthors(Policies, drf_viewsets.ModelViewSet):
    queryset = Author.objects.order_by("pk")
    serializer_class = AuthorSchema


class LineInput(serializers.Serializer):
    name = serializers.CharField(max_length=40)
    count = serializers.IntegerField(min_value=0)


class OrderInput(serializers.Serializer):
    lines = LineInput(many=True)


class MsgspecInput(OrderInput):
    class Meta:
        serializer_backend = "msgspec"


class PydanticInput(OrderInput):
    class Meta:
        serializer_backend = "pydantic"


class DRFValidation(Policies, DRFAPIView):
    def post(self, request):
        serializer = OrderInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.validated_data)


class Validation(Policies, APIView):
    serializer_class = OrderInput

    async def post(self, request):
        serializer = self.serializer_class(data=await request.adata())
        await aio.is_valid(serializer, raise_exception=True)
        return Response(serializer.validated_data)


def input_payloads():
    return {
        "canonical": {"lines": [{"name": "item", "count": 1}] * 30},
        "coercion": {"lines": [{"name": " item ", "count": "1"}] * 30},
        "invalid": {"lines": [{"name": "item", "count": "bad"}]},
    }


class Server(uvicorn.Server):
    def __init__(self, config):
        super().__init__(config)
        self.ready = threading.Event()

    async def startup(self, sockets=None):
        await super().startup(sockets)
        self.ready.set()


async def measure(base_url, counters, closed, report):
    async with httpx.AsyncClient(
        base_url=base_url, trust_env=False, timeout=10
    ) as client:
        reference = (await client.get("/drf/")).json()
        for concurrency in (1, 8):
            for route in ("drf", "aiodrf", "msgspec", "pydantic", "direct", "external"):
                timings = []
                statuses = {}
                semaphore = asyncio.Semaphore(concurrency)

                async def request(
                    semaphore=semaphore, route=route, timings=timings, statuses=statuses
                ):
                    async with semaphore:
                        started = time.perf_counter()
                        response = await client.get(f"/{route}/")
                        timings.append((time.perf_counter() - started) * 1000)
                        statuses[response.status_code] = (
                            statuses.get(response.status_code, 0) + 1
                        )
                        assert response.status_code == 200
                        assert response.json() == (
                            {"value": 1}
                            if route in ("direct", "external")
                            else reference
                        )

                # Three raw rounds; warm up outside the recorded calls.
                await client.get(f"/{route}/")
                rounds = []
                for _ in range(3):
                    started, cpu = time.perf_counter(), time.process_time()
                    await asyncio.gather(*(request() for _ in range(20)))
                    rounds.append(
                        {
                            "seconds": time.perf_counter() - started,
                            "cpu_seconds": time.process_time() - cpu,
                            "requests": 20,
                            "p95_ms": percentile(timings[-20:], 0.95),
                        }
                    )
                report["workloads"].append(
                    {
                        "route": route,
                        "concurrency": concurrency,
                        "statuses": statuses,
                        "rounds": rounds,
                        "latency_ms": timings,
                        "p50_ms": statistics.median(timings),
                        "p95_ms": percentile(timings, 0.95),
                        "p99_ms": percentile(timings, 0.99),
                    }
                )
        report["mixed"] = await mixed_load(client, reference)
        report["input"] = await input_load(client)
        crud = {}
        for route in ("drf-authors", "authors"):
            created = await client.post(f"/{route}/", json={"name": "created"})
            assert created.status_code == 201
            url = f"/{route}/{created.json()['id']}/"
            statuses = [
                (await client.patch(url, json={"name": "updated"})).status_code,
                (await client.get(url)).status_code,
                (await client.delete(url)).status_code,
            ]
            assert statuses == [200, 200, 204]
            crud[route] = [created.status_code, *statuses]
        report["crud_statuses"] = crud
        first_bytes, close_times, produced = [], [], []
        rss_before = rss_bytes()
        for _ in range(12):
            closed.clear()
            before = counters["advanced"]
            started = time.perf_counter()
            async with client.stream("GET", "/stream/") as response:
                assert response.status_code == 200
                await anext(response.aiter_bytes())
                first_bytes.append((time.perf_counter() - started) * 1000)
                await asyncio.sleep(
                    0.01
                )  # Deliberately slow client, not a race assertion.
            disconnected = time.perf_counter()
            assert await asyncio.to_thread(closed.wait, 3)
            close_times.append((time.perf_counter() - disconnected) * 1000)
            produced.append(counters["advanced"] - before)
            assert produced[-1] < 1_000_000
        assert counters["closed"] == 12
        report["streaming"] = {
            "aborted": 12,
            "closed": counters["closed"],
            "first_byte_ms": first_bytes,
            "close_ms": close_times,
            "produced": produced,
            "rss_before": rss_before,
            "rss_after": rss_bytes(),
            "threads": threading.active_count(),
        }


async def mixed_load(client, reference):
    """Equal offered work; retain per-route latency rather than a blended tail."""
    timings = {route: [] for route in ("drf", "aiodrf", "external")}
    semaphore = asyncio.Semaphore(8)

    async def request(route):
        queued = time.perf_counter()
        async with semaphore:
            started = time.perf_counter()
            response = await client.get(f"/{route}/")
            finished = time.perf_counter()
        assert response.status_code == 200
        assert response.json() == ({"value": 1} if route == "external" else reference)
        timings[route].append(
            {
                "service_ms": (finished - started) * 1000,
                "total_ms": (finished - queued) * 1000,
            }
        )

    started, cpu = time.perf_counter(), time.process_time()
    await asyncio.gather(*(request(route) for _ in range(40) for route in timings))
    return {
        "concurrency": 8,
        "seconds": time.perf_counter() - started,
        "cpu_seconds": time.process_time() - cpu,
        "completed": {route: len(values) for route, values in timings.items()},
        "latency": timings,
    }


async def input_load(client):
    results = []
    for name, payload in input_payloads().items():
        reference = await client.post("/input-drf/", json=payload)
        assert reference.status_code == (400 if name == "invalid" else 200)
        for backend in ("drf", "aiodrf", "msgspec", "pydantic"):
            timings = []
            for _ in range(20):
                started = time.perf_counter()
                response = await client.post(f"/input-{backend}/", json=payload)
                timings.append((time.perf_counter() - started) * 1000)
                assert response.status_code == reference.status_code
                assert response.json() == reference.json()
            results.append(
                {
                    "backend": backend,
                    "case": name,
                    "status": response.status_code,
                    "ms": timings,
                }
            )
    return results


def input_diagnostics():
    results = []
    # Peak traced Python allocations, not an allocation count or process RSS.
    # A separate pass avoids timing the allocator instrumentation itself.
    for backend, schema in (("msgspec", MsgspecInput), ("pydantic", PydanticInput)):
        for name, payload in input_payloads().items():
            recognized = inputs.recognize(schema(data=payload), backend=backend)
            tracemalloc.start()
            try:
                for _ in range(10):
                    aio.try_is_valid(schema(data=payload))
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            results.append(
                {
                    "backend": backend,
                    "case": name,
                    "recognized": recognized is not inputs.NOT_RECOGNIZED,
                    "peak_python_bytes": peak,
                }
            )
    return results


async def disabled_diagnostic_overhead():
    def noop():
        return 1

    wrappers = {
        "asgiref": sync_to_async(noop),
        "aiodrf_counter_disabled": run_sync(noop),
    }
    results = {name: [] for name in wrappers}
    for _ in range(5):
        for name, wrapped in wrappers.items():
            await wrapped()
            started = time.perf_counter()
            for _ in range(200):
                assert await wrapped() == 1
            results[name].append((time.perf_counter() - started) / 200 * 1_000_000)
    return results


def diagnostic_request(django_application):
    # Separate diagnostic requests; instrumentation does not contaminate timings.
    from tests.live.asgi import call

    original_hop, original_execute = SyncToAsync.__call__, CursorWrapper.execute
    totals = {"hops": 0, "queries": 0}

    async def hop(self, *args, **kwargs):
        totals["hops"] += 1
        return await original_hop(self, *args, **kwargs)

    def execute(self, *args, **kwargs):
        totals["queries"] += 1
        return original_execute(self, *args, **kwargs)

    async def probe():
        with count_hops() as hops:
            assert await call(django_application, "GET", "/aiodrf/") == 200
        return hops.count

    with (
        patch.object(SyncToAsync, "__call__", hop),
        patch.object(CursorWrapper, "execute", execute),
        override_settings(ALLOWED_HOSTS=["testserver"]),
    ):
        totals["framework_hops"] = asyncio.run(probe())
    return totals


@contextmanager
def application_fixture(counters, closed, loop_delays, base_url):
    upstream = None
    heartbeat = None

    async def stub(request):
        await asyncio.sleep(0.005)
        return JsonResponse({"value": 1})

    async def direct(request):
        result = await upstream.get("/stub/")
        return JsonResponse(result.json())

    class External(APIView):
        authentication_classes = []
        permission_classes = []

        async def get(self, request):
            result = await upstream.get("/stub/")
            return Response(result.json())

    class Stream(External):
        async def get(self, request):
            async def items():
                try:
                    for _ in range(1_000_000):
                        counters["advanced"] += 1
                        yield {"data": "x" * 1024}
                        await asyncio.sleep(0)
                finally:
                    counters["closed"] += 1
                    closed.set()

            return StreamingResponse(items())

    async def monitor():
        while True:
            started = time.perf_counter()
            await asyncio.sleep(0.005)
            loop_delays.append(max(0, time.perf_counter() - started - 0.005))

    routes = [
        path("stub/", stub),
        path("direct/", direct),
        path("external/", External.as_view()),
        path("stream/", Stream.as_view()),
        path("input-drf/", DRFValidation.as_view()),
    ]
    for backend, schema in (
        ("aiodrf", OrderInput),
        ("msgspec", MsgspecInput),
        ("pydantic", PydanticInput),
    ):
        routes.append(
            path(f"input-{backend}/", Validation.as_view(serializer_class=schema))
        )
    for prefix, klass, schema in (
        ("drf", DRFBooks, BookSchema),
        ("aiodrf", Books, BookSchema),
        ("msgspec", Books, CompiledBookSchema),
        ("pydantic", Books, PydanticBookSchema),
    ):
        routes.append(
            path(f"{prefix}/", klass.as_view({"get": "list"}, serializer_class=schema))
        )
    for prefix, klass in (("drf-authors", DRFAuthors), ("authors", Authors)):
        routes.extend(
            [
                path(f"{prefix}/", klass.as_view({"post": "create"})),
                path(
                    f"{prefix}/<int:pk>/",
                    klass.as_view(
                        {
                            "get": "retrieve",
                            "patch": "partial_update",
                            "delete": "destroy",
                        }
                    ),
                ),
            ]
        )

    with override_settings(
        ROOT_URLCONF=tuple(routes), ALLOWED_HOSTS=["127.0.0.1"], MIDDLEWARE=[]
    ):
        django_application = get_asgi_application()

        async def application(scope, receive, send):
            nonlocal upstream, heartbeat
            if scope["type"] != "lifespan":
                await django_application(scope, receive, send)
                return
            assert (await receive())["type"] == "lifespan.startup"
            async with httpx.AsyncClient(
                base_url=base_url, trust_env=False
            ) as upstream:
                heartbeat = asyncio.create_task(monitor())
                await send({"type": "lifespan.startup.complete"})
                try:
                    assert (await receive())["type"] == "lifespan.shutdown"
                finally:
                    heartbeat.cancel()
                    await asyncio.gather(heartbeat, return_exceptions=True)
            await send({"type": "lifespan.shutdown.complete"})

        def finished():
            return heartbeat is not None and heartbeat.done()

        yield application, django_application, finished


@pytest.fixture
def cpu_affinity():
    original_affinity = (
        os.sched_getaffinity(0) if hasattr(os, "sched_getaffinity") else None
    )
    try:
        if requested := os.environ.get("AIODRF_BENCH_CPUS"):
            os.sched_setaffinity(0, {int(cpu) for cpu in requested.split(",")})
        yield sorted(os.sched_getaffinity(0)) if original_affinity else None
    finally:
        if original_affinity is not None:
            os.sched_setaffinity(0, original_affinity)


@pytest.mark.django_db(transaction=True)
def test_real_server_contracts(tmp_path, cpu_affinity):
    if connection.vendor == "sqlite" and connection.is_in_memory_db():
        pytest.fail(
            "Use nox -s live, or tests.settings_live with AIODRF_LIVE_SQLITE set to a disposable file"
        )
    author = Author.objects.create(name="benchmark")
    Book.objects.bulk_create(
        Book(title=f"Book {index}", isbn=f"{index:013d}", author=author)
        for index in range(30)
    )
    counters = {"closed": 0, "advanced": 0}
    closed = threading.Event()
    loop_delays = []
    listener = socket.socket()
    # Supplied sockets bypass Uvicorn's bind_socket configuration. Disable
    # Nagle explicitly: otherwise separate headers/body trigger delayed ACKs.
    listener.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"
    with application_fixture(counters, closed, loop_delays, base_url) as (
        application,
        django_application,
        heartbeat_done,
    ):
        server = Server(
            uvicorn.Config(application, log_level="error", lifespan="on", ws="none")
        )
        thread = threading.Thread(
            target=server.run, kwargs={"sockets": [listener]}, daemon=True
        )
        thread.start()
        report = {
            "database": connection.vendor,
            "database_runtime": database_runtime(),
            "tcp_nodelay": True,
            "python": platform.python_version(),
            "gil": getattr(sys, "_is_gil_enabled", lambda: True)(),
            "platform": platform.platform(),
            "cpu_model": next(
                (
                    line.split(":", 1)[1].strip()
                    for line in Path("/proc/cpuinfo").read_text().splitlines()
                    if line.startswith("model name")
                ),
                "unknown",
            )
            if Path("/proc/cpuinfo").exists()
            else platform.processor(),
            "cpus": cpu_affinity,
            "versions": {
                name: version(name)
                for name in (
                    "django",
                    "djangorestframework",
                    "asgiref",
                    "msgspec",
                    "pydantic",
                    "uvicorn",
                    "httpx",
                )
            },
            "workloads": [],
        }

        try:
            assert server.ready.wait(10), "Uvicorn did not start"
            asyncio.run(measure(base_url, counters, closed, report))
        finally:
            server.should_exit = True
            thread.join(10)
            listener.close()
        assert not thread.is_alive()
        assert heartbeat_done()
        report["event_loop_delay_ms"] = {
            "p95": percentile(loop_delays, 0.95) * 1000,
            "max": max(loop_delays) * 1000,
        }

        report["diagnostic_list_request"] = diagnostic_request(django_application)
        report["input_diagnostics"] = input_diagnostics()
        report["disabled_diagnostics_us"] = asyncio.run(disabled_diagnostic_overhead())

    report["source_sha256"] = {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(Path("src/aiodrf").rglob("*.py"))
    }
    report["harness_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    destination = Path(
        os.environ.get("AIODRF_BENCH_OUTPUT", str(tmp_path / "contracts.json"))
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x") as output:
        json.dump(report, output, indent=2)
    print(f"Measured report: {destination}")
