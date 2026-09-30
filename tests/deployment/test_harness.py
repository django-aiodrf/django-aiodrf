"""Safety and reporting checks that need no Docker or PostgreSQL connection."""

import asyncio
import json
import sys
import threading
from contextlib import asynccontextmanager
from inspect import iscoroutinefunction
from io import StringIO
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import User
from django.db import connections
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import path
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated

from aiodrf.response import Response
from aiodrf.test import AsyncAPIClient
from aiodrf.utils import run_sync
from tests.deployment import (
    processes,
    soak,
    views,
)
from tests.deployment.lifecycle import Resources, monitor
from tests.deployment.processes import Deployment
from tests.deployment.views import ThreadedJSONRenderer
from tests.testapp.models import Author


@pytest.mark.parametrize("module", [soak])
def test_report_cli_refuses_to_overwrite_existing_artifacts(
    tmp_path, monkeypatch, module
):
    path = tmp_path / "result.json"
    path.write_text("original")
    monkeypatch.setattr(sys, "argv", ["tool", "--output", str(path)])
    with pytest.raises(SystemExit) as exc:
        module.main()
    assert exc.value.code == 2
    assert path.read_text() == "original"


@pytest.mark.parametrize("phase", ["start", "close"])
async def test_deployment_cancellation_joins_owned_thread_before_returning(
    tmp_path, monkeypatch, phase
):
    entered, release, finished = (threading.Event() for _ in range(3))
    calls = []

    def work(name):
        calls.append(name)
        if name == phase:
            entered.set()
            assert release.wait(3)
            finished.set()

    async def ready():
        pass

    service = SimpleNamespace(
        start=lambda: work("start"), close=lambda: work("close"), ready=ready
    )
    monkeypatch.setattr(processes, "Deployment", lambda *args, **kwargs: service)

    async def use():
        async with processes.deployment(tmp_path):
            assert phase == "close", "startup cancellation must not yield a deployment"

    task = asyncio.create_task(use())
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert finished.is_set()
        assert calls == ["start", "close"]
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize(
    "args",
    [
        ["--seconds", "nan"],
        ["--seconds", "0"],
        ["--seconds", "86401"],
        ["--cycle-seconds", "inf"],
        ["--streams", "33"],
    ],
)
def test_soak_cli_rejects_unbounded_or_invalid_workload(tmp_path, monkeypatch, args):
    path = tmp_path / "result.jsonl"
    monkeypatch.setattr(sys, "argv", ["tool", "--output", str(path), *args])
    with pytest.raises(SystemExit) as exc:
        soak.main()
    assert exc.value.code == 2
    assert not path.exists()


def test_signal_target_must_be_an_owned_worker(tmp_path, monkeypatch):
    service = Deployment(tmp_path)
    monkeypatch.setattr(service, "active_pids", lambda: {123})
    with pytest.raises(RuntimeError, match="not owned"):
        service.kill_worker(456)


@pytest.mark.parametrize("delay", [-1, 2, float("nan"), float("inf")])
def test_upstream_delay_is_bounded(tmp_path, delay):
    with pytest.raises(ValueError, match="Upstream delay"):
        Deployment(tmp_path, upstream_delay=delay)


@pytest.mark.parametrize("delay", [-1, 0.5, float("nan"), float("inf")])
def test_db_delay_is_bounded(tmp_path, delay):
    with pytest.raises(ValueError, match="DB delay"):
        Deployment(tmp_path, db_delay=delay)


async def test_the_latency_proxy_delays_each_direction_in_order():
    from tests.deployment import latency_proxy

    async def echo(reader, writer):
        while data := await reader.read(1024):
            writer.write(data)
            await writer.drain()
        writer.close()

    target = await asyncio.start_server(echo, "127.0.0.1", 0)
    options = SimpleNamespace(
        port=0,
        target_host="127.0.0.1",
        target_port=target.sockets[0].getsockname()[1],
        delay=0.05,
    )
    proxy = await latency_proxy.serve(options)
    try:
        port = proxy.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        loop = asyncio.get_running_loop()
        started = loop.time()
        for part in (b"a", b"b", b"c"):
            writer.write(part)
        await writer.drain()
        received = b""
        while len(received) < 3:
            received += await reader.read(3)
        elapsed = loop.time() - started
        writer.close()
        # Pipelined: three chunks take one round trip, not three.
        assert received == b"abc"
        assert 0.1 <= elapsed < 0.3
    finally:
        proxy.close()
        await proxy.wait_closed()
        target.close()
        await target.wait_closed()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"middleware_mode": "typo"},
        {"loop": "auto"},
        {"http": "auto"},
        {"http_client": "typo"},
        {"db_backend": "typo"},
    ],
)
def test_comparison_requires_explicit_known_implementations(tmp_path, kwargs):
    with pytest.raises(
        ValueError, match=r"Middleware mode|explicit supported|HTTP client|DB backend"
    ):
        Deployment(tmp_path, **kwargs)


@pytest.fixture
async def worker_connections(transactional_db):
    try:
        yield
    finally:
        await run_sync(connections.close_all)()


async def test_db_routes_have_equal_payloads_and_query_counts(worker_connections):
    author = await Author.objects.acreate(name="measured")
    user = await User.objects.acreate(username="benchmark")
    policies = {
        "authentication_classes": [SessionAuthentication],
        "permission_classes": [IsAuthenticated],
    }
    urls = (
        path("drf/", views.DRFAuthors.as_view(**policies)),
        path("aiodrf/", views.Authors.as_view(**policies)),
        path("drf-detail/<int:pk>/", views.DRFAuthorDetail.as_view(**policies)),
        path("aiodrf-detail/<int:pk>/", views.AuthorDetail.as_view(**policies)),
        path("drf-read/", views.DRFRead.as_view(**policies)),
        path("aiodrf-read/", views.AsyncRead.as_view(**policies)),
        path("drf-read/<int:pk>/", views.DRFRead.as_view(**policies)),
        path("aiodrf-read/<int:pk>/", views.AsyncRead.as_view(**policies)),
    )
    with override_settings(ROOT_URLCONF=urls):
        client = AsyncAPIClient()
        for url in (
            "/drf/",
            "/aiodrf/",
            f"/drf-detail/{author.pk}/",
            f"/aiodrf-detail/{author.pk}/",
        ):
            assert (await client.get(url)).status_code == 403
        await client.aforce_login(user)
        for pair in (
            ("/drf/", "/aiodrf/"),
            (f"/drf-detail/{author.pk}/", f"/aiodrf-detail/{author.pk}/"),
            ("/drf-read/", "/aiodrf-read/"),
            (f"/drf-read/{author.pk}/", f"/aiodrf-read/{author.pk}/"),
        ):
            payloads = []
            for url in pair:

                def begin_capture():
                    capture = CaptureQueriesContext(connections["default"])
                    capture.__enter__()
                    return capture

                capture = await run_sync(begin_capture)()
                try:
                    response = await client.get(url)
                finally:
                    await run_sync(capture.__exit__)(None, None, None)
                assert response.status_code == 200
                # Session, authenticated user, then one list/detail SELECT.
                assert len(capture) == 3, capture.captured_queries
                assert all(row["sql"].lstrip().startswith("SELECT") for row in capture)
                payloads.append(response.content)
            assert payloads[0] == payloads[1]


def test_single_worker_is_the_owned_server_process(tmp_path):
    service = Deployment(tmp_path, workers=1)
    service.server = SimpleNamespace(pid=123, poll=lambda: None)
    assert service.owns_worker(123)
    service.server.poll = lambda: 0
    assert not service.owns_worker(123)


def test_soak_progress_is_independently_readable_json_lines():
    stream = StringIO()
    soak.emit(stream, {"type": "header"})
    soak.emit(stream, {"type": "cycle", "cycle": 1})
    records = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert records == [{"type": "header"}, {"type": "cycle", "cycle": 1}]
    assert not any(record["type"] == "complete" for record in records)


def test_fairness_thread_variant_really_uses_djangos_worker_path():
    response = Response({"text": "x"})
    response.accepted_renderer = ThreadedJSONRenderer()
    assert not iscoroutinefunction(response.render)


async def test_lag_reset_excludes_an_inflight_sample_from_the_previous_phase(
    monkeypatch,
):
    resources = Resources(client=None, upstream="unused", sync_client=None)
    calls = 0

    class StopMonitor(Exception):
        pass

    async def tick(_):
        nonlocal calls
        calls += 1
        if calls == 1:
            resources.lag_epoch += 1
        elif calls == 2:
            assert not resources.lag_ms
        else:
            raise StopMonitor

    monkeypatch.setattr("tests.deployment.lifecycle.asyncio.sleep", tick)
    with pytest.raises(StopMonitor):
        await monitor(resources)
    assert len(resources.lag_ms) == 1


async def test_profile_startup_failure_closes_both_http_clients(tmp_path, monkeypatch):
    from tests.deployment import lifecycle

    closed = []

    @asynccontextmanager
    async def server():
        yield SimpleNamespace(
            sockets=[SimpleNamespace(getsockname=lambda: ("127.0.0.1", 1))]
        )

    async def start_server(*args):
        return server()

    @asynccontextmanager
    async def async_client(**kwargs):
        try:
            yield object()
        finally:
            closed.append("async")

    def fail_profile(mode):
        raise RuntimeError("profiler failed")

    monkeypatch.setattr(lifecycle.asyncio, "start_server", start_server)
    monkeypatch.setattr(lifecycle.httpx, "AsyncClient", async_client)
    monkeypatch.setattr(
        lifecycle.httpx,
        "Client",
        lambda **kwargs: SimpleNamespace(close=lambda: closed.append("sync")),
    )
    monkeypatch.setattr(lifecycle, "start_profile", fail_profile)
    with (
        override_settings(CONFIG={"profile": "cpu", "directory": str(tmp_path)}),
        pytest.raises(RuntimeError, match="profiler failed"),
    ):
        async with lifecycle.lifespan():
            raise AssertionError("startup should fail before yielding")
    assert closed == ["sync", "async"]
