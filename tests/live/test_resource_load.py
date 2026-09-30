"""Separate-process load and SIGTERM gates; metrics are observations, not SLAs.

AIODRF_RESOURCE_STREAMS, AIODRF_RESOURCE_SECONDS and AIODRF_RESOURCE_OUTPUT
select a larger local run. The default CI profile is deliberately bounded.
"""

import asyncio
import contextlib
import hashlib
import json
import multiprocessing
import os
import platform
import signal
import socket
import sys
import time
from importlib.metadata import version
from pathlib import Path

import httpx
import pytest

from tests.live.resource_app import serve
from tests.live.resource_workload import mixed_load, percentiles, process_metrics

pytestmark = pytest.mark.skipif(os.name != "posix", reason="SIGTERM process contract")


class Worker:
    def __init__(self, directory, index):
        self.socket = socket.socket()
        # As in the live-contract harness, supplied listeners must not add
        # Nagle/delayed-ACK latency between separately written headers/body.
        self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen(256)
        self.url = f"http://127.0.0.1:{self.socket.getsockname()[1]}"
        self.journal = directory / f"worker-{index}.json"
        self.process = multiprocessing.get_context("spawn").Process(
            target=serve,
            args=(
                self.socket,
                str(directory / f"worker-{index}.sqlite3"),
                str(self.journal),
            ),
        )

    async def __aenter__(self):
        self.process.start()
        self.socket.close()
        try:
            async with httpx.AsyncClient(timeout=0.25) as client:
                async with asyncio.timeout(20):
                    while self.process.is_alive():
                        try:
                            response = await client.get(self.url + "/metrics/")
                            if response.status_code == 200:
                                return self
                        except httpx.TransportError:
                            pass
                        await asyncio.sleep(0.02)
            raise RuntimeError("Load-test worker exited before startup")  # noqa: TRY301 -- cleanup boundary
        except BaseException:
            await self.__aexit__(None, None, None)
            raise

    async def stop(self):
        if self.process.is_alive():
            self.process.terminate()  # SIGTERM reaches Uvicorn's lifecycle handler.
        await asyncio.to_thread(self.process.join, 10)
        assert not self.process.is_alive(), (
            "Worker did not finish within its shutdown budget"
        )
        assert self.process.exitcode in (0, -signal.SIGTERM)
        return json.loads(self.journal.read_text())

    async def __aexit__(self, *exc):
        try:
            if self.process.is_alive():
                await self.stop()
        finally:
            if self.process.is_alive():
                self.process.kill()  # Only the test-owned child, on test failure.
                await asyncio.to_thread(self.process.join, 5)
            self.process.close()


async def open_stream(client, stack, url):
    response = await stack.enter_async_context(client.stream("GET", url + "/stream/"))
    assert response.status_code == 200
    iterator = response.aiter_lines()
    await stack.enter_async_context(contextlib.aclosing(iterator))
    async with asyncio.timeout(10):
        async for line in iterator:
            if line.startswith("data:"):
                assert json.loads(line.removeprefix("data:")) == {"ready": True}
                return
    raise AssertionError("Stream ended before its first event")


async def test_mixed_load_with_open_streams_and_large_payloads(tmp_path):
    stream_count = int(os.environ.get("AIODRF_RESOURCE_STREAMS", "32"))
    seconds = float(os.environ.get("AIODRF_RESOURCE_SECONDS", "3"))
    assert 1 <= stream_count <= 512
    assert 0 < seconds <= 3600
    async with Worker(tmp_path, 0) as worker:
        async with httpx.AsyncClient(
            timeout=15, limits=httpx.Limits(max_connections=stream_count + 16)
        ) as client:
            baseline = process_metrics(worker.process.pid)
            first_bytes = []
            samples = []

            async def sample():
                while True:
                    samples.append(process_metrics(worker.process.pid))
                    await asyncio.sleep(0.05)

            async with contextlib.AsyncExitStack() as streams:

                async def start_one():
                    started = time.perf_counter()
                    await open_stream(client, streams, worker.url)
                    first_bytes.append((time.perf_counter() - started) * 1000)

                await asyncio.gather(*(start_one() for _ in range(stream_count)))
                held = (await client.get(worker.url + "/metrics/")).json()
                assert held["opened"] - held["closed"] == stream_count
                sampler = asyncio.create_task(sample())
                try:
                    load = await mixed_load(client, worker.url, seconds)
                    await large_payloads(client, worker.url)
                    during = (await client.get(worker.url + "/metrics/")).json()
                finally:
                    sampler.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await sampler
            async with asyncio.timeout(10):
                while True:
                    after = (await client.get(worker.url + "/metrics/")).json()
                    if after["closed"] == stream_count:
                        break
                    await asyncio.sleep(0.02)
        await asyncio.sleep(0.05)
        settled = process_metrics(worker.process.pid)
        journal = await worker.stop()
    assert journal["opened"] == journal["closed"] == stream_count
    assert journal["background_done"]
    assert journal["client_closed"]
    # Account for warm HTTP pools/control requests; catch one retained worker
    # or descriptor per stream without imposing machine-specific latency/RSS SLAs.
    if baseline["threads"] is not None:
        assert settled["threads"] <= baseline["threads"] + 4
        assert settled["fds"] <= baseline["fds"] + 4
    report = {
        "streams": stream_count,
        "seconds_requested": seconds,
        "load": load,
        "first_event": percentiles(first_bytes),
        "first_event_ms": first_bytes,
        "baseline": baseline,
        "settled": settled,
        "process_samples": samples,
        "event_loop_lag": percentiles(during["lag_ms"]),
        "event_loop_lag_ms": during["lag_ms"],
        "server_cpu_seconds": during["cpu_seconds"] - held["cpu_seconds"],
        "shutdown": journal,
    }
    await asyncio.to_thread(write_report, report)


async def large_payloads(client, url):
    payload = b"x" * (2 * 1024 * 1024)
    for kwargs in (
        {"json": {"text": payload.decode()}},
        {"files": {"file": ("large.bin", payload)}},
    ):
        result = await client.post(url + "/payload/", **kwargs)
        assert result.status_code == 200
        assert result.json() == {"size": len(payload)}


def write_report(report):
    project = Path(__file__).resolve().parents[2]
    report.update(
        {
            "python": platform.python_version(),
            "gil_enabled": sys._is_gil_enabled(),
            "platform": platform.platform(),
            "versions": {
                name: version(name)
                for name in (
                    "django",
                    "djangorestframework",
                    "asgiref",
                    "httpx",
                    "uvicorn",
                )
            },
            "source_sha256": {
                str(file.relative_to(project)): hashlib.sha256(
                    file.read_bytes()
                ).hexdigest()
                for file in sorted((project / "src/aiodrf").rglob("*.py"))
            },
            "harness_sha256": {
                file.name: hashlib.sha256(file.read_bytes()).hexdigest()
                for file in (
                    Path(__file__),
                    Path(__file__).with_name("resource_app.py"),
                    Path(__file__).with_name("resource_workload.py"),
                )
            },
            "cpu_affinity": sorted(os.sched_getaffinity(0))
            if hasattr(os, "sched_getaffinity")
            else None,
        }
    )
    if output := os.environ.get("AIODRF_RESOURCE_OUTPUT"):
        with Path(output).open("x", encoding="utf-8") as destination:
            json.dump(report, destination, indent=2)
    print(
        json.dumps(
            {
                "streams": report["streams"],
                "baseline": report["baseline"],
                "settled": report["settled"],
                "event_loop_lag": report["event_loop_lag"],
            }
        )
    )


async def test_two_processes_close_loop_owned_resources_on_sigterm(tmp_path):
    async with contextlib.AsyncExitStack() as stack:
        workers = [
            await stack.enter_async_context(Worker(tmp_path, index))
            for index in range(2)
        ]
        async with (
            httpx.AsyncClient(timeout=10) as client,
            contextlib.AsyncExitStack() as streams,
        ):
            for worker in workers:
                await open_stream(client, streams, worker.url)
            # Leave connections open: Uvicorn's finite graceful deadline
            # cancels their producers before it runs lifespan shutdown.
            records = await asyncio.gather(*(worker.stop() for worker in workers))
    assert records[0]["pid"] != records[1]["pid"]
    assert all(record["opened"] == record["closed"] == 1 for record in records)
    assert all(
        record["background_done"] and record["client_closed"] for record in records
    )
