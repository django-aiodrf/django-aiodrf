"""Real supervisor/proxy contracts. Run explicitly; never in the core suite."""

import asyncio
import contextlib
import json
import time

import httpx
import pytest

from tests.deployment.processes import deployment
from tests.deployment.traffic import first_event, worker_snapshots


@pytest.mark.parametrize("middleware_mode", ["standard", "sync"])
async def test_real_supervisor_restart_and_shutdown_close_worker_resources(
    tmp_path, middleware_mode
):
    async with deployment(tmp_path, middleware_mode=middleware_mode) as service:
        async with httpx.AsyncClient(timeout=5) as anonymous:
            assert (await anonymous.get(service.url + "/aiodrf/")).status_code == 403
        async with httpx.AsyncClient(cookies=service.cookies, timeout=10) as client:
            initial = await worker_snapshots(client, service)
            assert len(initial) == 2
            assert all(record["user"] == "load-test" for record in initial.values())
            async with contextlib.AsyncExitStack() as streams:
                for _ in range(8):
                    await first_event(client, streams, service.url + "/stream/")
                service.restart()
                successes = 0
                async with asyncio.timeout(20):
                    while (
                        service.active_pids() & initial.keys()
                        or len(service.active_pids()) != 2
                    ):
                        response = await client.get(service.url + "/aiodrf/")
                        assert response.status_code == 200, response.text
                        assert len(response.json()) == 30
                        successes += 1
                        await asyncio.sleep(0.02)
                assert successes > 0
            retired = {record["pid"]: record for record in service.records("closed")}
            assert initial.keys() <= retired.keys()
            assert sum(retired[pid]["opened"] for pid in initial) == 8
            assert all(
                retired[pid]["opened"] == retired[pid]["closed"] for pid in initial
            )
            async with contextlib.AsyncExitStack() as streams:
                for _ in range(8):
                    await first_event(client, streams, service.url + "/stream/")
                shutdown_seconds = await asyncio.to_thread(service.stop_server)
                assert shutdown_seconds < 15
        started = {record["pid"] for record in service.records("started")}
        closed = {record["pid"]: record for record in service.records("closed")}
        assert len(started) == 4
        assert started == closed.keys()
        assert all(record["opened"] == record["closed"] for record in closed.values())
        assert all(
            record["background_done"]
            and record["client_closed"]
            and record["sync_client_closed"]
            for record in closed.values()
        )


@pytest.mark.parametrize("middleware_mode", ["standard", "sync"])
async def test_nginx_buffering_and_authenticated_large_uploads(
    tmp_path, middleware_mode
):
    async with (
        deployment(tmp_path, middleware_mode=middleware_mode) as service,
        httpx.AsyncClient(cookies=service.cookies, timeout=10) as client,
    ):
        times = {}
        for prefix in ("", "/buffered"):
            started = time.monotonic()
            async with contextlib.AsyncExitStack() as stack:
                _, iterator = await first_event(
                    client, stack, service.url + prefix + "/stream/?limit=3&interval=1"
                )
                times[prefix] = time.monotonic() - started
                if not prefix:
                    # Received before the finite producer completes (3 s).
                    assert times[prefix] < 2
                # proxy_buffering=on permits buffering; it does not promise
                # that every small chunk is delayed on every Nginx version.
                # Assert wire correctness on both paths, not invented latency.
                # Drain the finite response, verifying valid remaining frames.
                events = [
                    json.loads(line[5:])
                    async for line in iterator
                    if line.startswith("data:")
                ]
                assert [event["sequence"] for event in events] == [0, 1, 2]
        metrics = await client.get(service.url + "/metrics/")
        token = metrics.json()["csrf"]
        assert (
            await client.post(service.url + "/payload/", json={"text": "x"})
        ).status_code == 403
        for kwargs in (
            {"json": {"text": "x" * (2 * 1024 * 1024)}},
            {"files": {"file": ("large.bin", b"x" * (2 * 1024 * 1024))}},
        ):
            response = await client.post(
                service.url + "/payload/", headers={"X-CSRFToken": token}, **kwargs
            )
            assert response.status_code == 200, response.text
            assert response.json() == {"size": 2 * 1024 * 1024}
        snapshots = await worker_snapshots(client, service)
        assert sum(record["opened"] for record in snapshots.values()) == 2
        assert all(
            record["opened"] == record["closed"] for record in snapshots.values()
        )
        print(
            json.dumps({"nginx": service.nginx_version, "first_event_seconds": times})
        )


async def test_supervisor_replaces_crashed_worker_without_claiming_cleanup(tmp_path):
    async with deployment(tmp_path) as service:
        original = service.active_pids()
        async with (
            httpx.AsyncClient(cookies=service.cookies, timeout=5) as client,
            contextlib.AsyncExitStack() as streams,
        ):
            victim, iterator = await first_event(
                client, streams, service.url + "/stream/"
            )
            service.kill_worker(victim)
            # The in-flight stream is interrupted, not migrated/replayed by the
            # supervisor. Depending on proxy framing, EOF or a transport error
            # reaches the client; neither is an ordinary DRF error response.
            async with asyncio.timeout(5):
                with contextlib.suppress(httpx.RemoteProtocolError, httpx.ReadError):
                    async for _ in iterator:
                        pass
            async with asyncio.timeout(15):
                while (  # noqa: ASYNC110 -- external worker replacement has no asyncio event
                    len(service.active_pids()) != 2 or service.active_pids() == original
                ):
                    await asyncio.sleep(0.05)
            snapshots = await worker_snapshots(client, service)
            assert snapshots.keys() == service.active_pids()
            assert victim not in snapshots
        await asyncio.to_thread(service.stop_server)
        started = {record["pid"] for record in service.records("started")}
        closed = {record["pid"] for record in service.records("closed")}
        assert len(started) == 3
        # SIGKILL cannot run a context manager's finally; do not report otherwise.
        assert closed == started - {victim}
