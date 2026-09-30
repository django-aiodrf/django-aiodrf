"""
The database connection budget of a deployment: pools are per worker, so the
server's limit is workers x pool size. Real Uvicorn workers, Nginx and
PostgreSQL (``tests/services/compose.yaml``); uses only test-owned databases.
"""

import asyncio
import threading
import time

import httpx

from tests.deployment.processes import deployment

WORKERS, POOL = 2, 4


def sample_sessions(service, stop, samples):
    # From a thread: the harness's admin connection is synchronous.
    while not stop.is_set():
        samples.append(sum(service.database_metrics().values()))
        time.sleep(0.01)


async def test_many_authenticated_requests_stay_within_workers_times_pool(tmp_path):
    async with (
        deployment(tmp_path, workers=WORKERS, pool_size=POOL) as service,
        httpx.AsyncClient(
            cookies=service.cookies, timeout=30, trust_env=False
        ) as client,
    ):
        stop, samples = threading.Event(), []
        sampler = threading.Thread(
            target=sample_sessions, args=(service, stop, samples)
        )
        sampler.start()
        try:
            # Session authentication reads the database on every request.
            responses = await asyncio.gather(
                *(
                    client.get(service.url + path)
                    for path in ("/aiodrf/", "/external/") * 32
                )
            )
        finally:
            stop.set()
            sampler.join()
        assert {response.status_code for response in responses} == {200}
        assert samples
        assert max(samples) <= WORKERS * POOL, max(samples)


async def test_open_authenticated_streams_do_not_hold_pooled_connections(tmp_path):
    # Twice as many open, session-authenticated streams as pooled connections:
    # each borrowed a connection to authenticate and gave it back, so all of
    # them open and the next request is served without waiting.
    async with (
        deployment(tmp_path, workers=1, pool_size=2) as service,
        httpx.AsyncClient(
            cookies=service.cookies, timeout=30, trust_env=False
        ) as client,
    ):
        streams = []

        async def open_stream():
            request = client.build_request(
                "GET", service.url + "/stream/", params={"interval": 0.5}
            )
            response = await client.send(request, stream=True)
            streams.append(response)
            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    return

        try:
            async with asyncio.TaskGroup() as tasks:
                for _ in range(4):
                    tasks.create_task(open_stream())
            started = time.monotonic()
            response = await client.get(service.url + "/aiodrf/", timeout=10)
            elapsed = time.monotonic() - started
            pool = (await client.get(service.url + "/metrics/")).json()["pool"]
        finally:
            for stream in streams:
                await stream.aclose()
        assert response.status_code == 200
        assert elapsed < 1, elapsed
        assert pool["pool_max"] == 2
        assert pool["requests_waiting"] == 0
