"""Shared workload and process measurements for the local HTTP harnesses."""

import asyncio
import os
import random
import time
from pathlib import Path


def process_metrics(pid):
    proc = Path(f"/proc/{pid}")
    if not proc.is_dir():
        return {"rss_bytes": None, "threads": None, "fds": None}
    return {
        "rss_bytes": int((proc / "statm").read_text().split()[1])
        * os.sysconf("SC_PAGE_SIZE"),
        "threads": len(list((proc / "task").iterdir())),
        "fds": len(list((proc / "fd").iterdir())),
    }


def percentiles(values):
    ordered = sorted(values)
    return (
        {
            f"p{percentile}_ms": ordered[
                min(len(ordered) - 1, int(len(ordered) * percentile / 100))
            ]
            for percentile in (50, 95, 99)
        }
        if ordered
        else {}
    )


async def mixed_load(client, url, seconds):
    routes = ("drf", "aiodrf", "external")
    records = {route: [] for route in routes}
    limiter = asyncio.Semaphore(8)
    rng = random.Random(0)  # noqa: S311 -- reproducible route order, not a secret

    async def request(route):
        queued = time.perf_counter()
        async with limiter:
            started = time.perf_counter()
            response = await client.get(f"{url}/{route}/")
            finished = time.perf_counter()
        assert response.status_code == 200, response.text
        expected = (
            {"body": "ok"}
            if route == "external"
            else [{"id": index + 1, "name": f"author-{index}"} for index in range(30)]
        )
        assert response.json() == expected
        records[route].append(
            {
                "service_ms": (finished - started) * 1000,
                "admission_ms": (started - queued) * 1000,
                "total_ms": (finished - queued) * 1000,
            }
        )

    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        batch = list(routes) * 8
        rng.shuffle(batch)
        async with asyncio.TaskGroup() as group:
            for route in batch:
                group.create_task(request(route))
    return {
        route: {
            "requests": len(values),
            "samples": values,
            **percentiles([v["total_ms"] for v in values]),
        }
        for route, values in records.items()
    }
