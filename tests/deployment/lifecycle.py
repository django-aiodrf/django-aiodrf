"""One HTTP client and monitor per worker, with observable cleanup ownership."""

import asyncio
import contextlib
import json
import os
import time
from collections import deque
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

import httpx
from django.conf import settings

from aiodrf.utils import run_sync
from tests.deployment.profilers import start_profile, stop_profile
from tests.live.resource_app import upstream


@dataclass
class Resources:
    client: httpx.AsyncClient
    upstream: str
    sync_client: httpx.Client
    aio_client: object = None
    opened: int = 0
    closed: int = 0
    lag_ms: deque = field(default_factory=lambda: deque(maxlen=1000))
    lag_epoch: int = 0


async def monitor(resources):
    loop = asyncio.get_running_loop()
    while True:
        epoch = resources.lag_epoch
        target = loop.time() + 0.01
        await asyncio.sleep(0.01)
        # A reset must not charge the preceding workload's pending timer to
        # the next phase. The ordinary soak never resets this bounded window.
        if epoch == resources.lag_epoch:
            resources.lag_ms.append(max(0, loop.time() - target) * 1000)


def record(phase, **values):
    path = Path(settings.CONFIG["directory"]) / f"{phase}-{os.getpid()}.json"
    with path.open("x", encoding="utf-8") as destination:
        json.dump({"pid": os.getpid(), "at": time.time(), **values}, destination)


@contextlib.asynccontextmanager
async def lifespan():
    handler = partial(upstream, delay=settings.CONFIG.get("upstream_delay", 0.005))
    async with await asyncio.start_server(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        async with (
            httpx.AsyncClient(timeout=5, trust_env=False) as client,
            contextlib.AsyncExitStack() as cleanup,
        ):
            sync_client = httpx.Client(timeout=5, trust_env=False)
            cleanup.push_async_callback(run_sync(sync_client.close))
            resources = Resources(client, f"http://127.0.0.1:{port}/", sync_client)
            if settings.CONFIG.get("db_backend") == "async":
                from django_async_backend.db import async_connections

                cleanup.push_async_callback(async_connections["default"].close_pool)
            if settings.CONFIG.get("http_client", "httpx") == "aiohttp":
                import aiohttp

                resources.aio_client = await cleanup.enter_async_context(
                    aiohttp.ClientSession(
                        timeout=aiohttp.ClientTimeout(total=5), trust_env=False
                    )
                )
            mode = settings.CONFIG["profile"]
            if mode:
                start_profile(mode)
            task = asyncio.create_task(monitor(resources))
            try:
                await run_sync(record)("started")
                yield resources
            finally:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
                if mode:
                    values = await run_sync(stop_profile)(mode)
                    await run_sync(record)("profile", **values)
        await run_sync(record)(
            "closed",
            opened=resources.opened,
            closed=resources.closed,
            background_done=task.done(),
            client_closed=client.is_closed,
            sync_client_closed=sync_client.is_closed,
            aio_client_closed=resources.aio_client is None
            or resources.aio_client.closed,
        )
