"""Bounded-memory, repeatable authenticated stream churn through Nginx.

Reports are JSON Lines, flushed after each cycle. An absent complete footer
means the run did not finish. No per-request history accumulates across cycles.
"""

import argparse
import asyncio
import contextlib
import json
import tempfile
import time
from pathlib import Path

import httpx

from tests.deployment.processes import deployment, identity
from tests.deployment.traffic import first_event, worker_snapshots
from tests.live.resource_workload import mixed_load, percentiles


async def slow_reader(iterator):
    expected = 0
    async for line in iterator:
        if line.startswith("data:"):
            event = json.loads(line[5:])
            assert event["sequence"] == expected
            expected += 1
            await asyncio.sleep(0.1)


async def cycle(service, *, streams, seconds):
    first_events = []
    snapshots = []
    readers = []
    async with httpx.AsyncClient(
        cookies=service.cookies,
        timeout=15,
        limits=httpx.Limits(max_connections=streams + 16),
    ) as client:
        async with contextlib.AsyncExitStack() as stack:

            async def connect(index):
                start = time.monotonic()
                pid, iterator = await first_event(
                    client, stack, service.url + "/stream/?padding=4096&interval=0.01"
                )
                first_events.append((time.monotonic() - start) * 1000)
                # Half consume slowly; half stop reading after the first event.
                if index % 2:
                    readers.append(asyncio.create_task(slow_reader(iterator)))
                return pid

            async def sample():
                while True:
                    snapshots.append(
                        {
                            "elapsed": time.monotonic() - started,
                            "workers": await asyncio.to_thread(service.metrics),
                            "database_connections": await asyncio.to_thread(
                                service.database_metrics
                            ),
                        }
                    )
                    await asyncio.sleep(1)

            started = time.monotonic()
            sampler = None
            try:
                async with asyncio.TaskGroup() as group:
                    connections = [
                        group.create_task(connect(index)) for index in range(streams)
                    ]
                pids = [task.result() for task in connections]
                sampler = asyncio.create_task(sample())
                traffic = await mixed_load(client, service.url, seconds)
            finally:
                tasks = [*readers, *([sampler] if sampler is not None else [])]
                for task in tasks:
                    task.cancel()
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for result in results:
                    if isinstance(result, BaseException) and not isinstance(
                        result, asyncio.CancelledError
                    ):
                        raise result
        # Give disconnected requests time to return their DB connections.
        async with asyncio.timeout(15):
            while True:
                workers = await worker_snapshots(client, service)
                if all(
                    value["opened"] == value["closed"] for value in workers.values()
                ):
                    break
                await asyncio.sleep(0.05)
    await asyncio.sleep(0.1)
    # Retain summaries and a bounded sample, not every latency in a 2-hour run.
    for result in traffic.values():
        samples = result.pop("samples")
        result["service"] = percentiles([sample["service_ms"] for sample in samples])
        result["admission"] = percentiles(
            [sample["admission_ms"] for sample in samples]
        )
        result["sample_first_32"] = samples[:32]
    return {
        "elapsed": time.monotonic() - started,
        "streams_by_pid": {str(pid): pids.count(pid) for pid in set(pids)},
        "first_event": percentiles(first_events),
        "traffic": traffic,
        "samples": snapshots,
        "settled": await asyncio.to_thread(service.metrics),
        "database_settled": await asyncio.to_thread(service.database_metrics),
        "workers": {
            str(pid): {
                "opened": record["opened"],
                "closed": record["closed"],
                "loop_lag": percentiles(record["lag_ms"]),
            }
            for pid, record in workers.items()
        },
    }


def emit(destination, record):
    destination.write(json.dumps(record) + "\n")
    destination.flush()


async def run(options):
    with (
        tempfile.TemporaryDirectory(prefix="aiodrf-deployment-") as directory,
        options.output.open("x", encoding="utf-8") as destination,
    ):
        async with deployment(directory, pool_size=options.streams + 12) as service:
            emit(
                destination,
                {
                    "type": "header",
                    **identity(),
                    "nginx": service.nginx_version,
                    "nginx_image": service.image_id,
                    "postgres": service.pg_version,
                    "requested_seconds": options.seconds,
                    "cycle_seconds": options.cycle_seconds,
                    "streams_per_cycle": options.streams,
                    "workers": service.workers,
                    "pool_max_per_worker": service.pool_size,
                    "authentication": "database session; synthetic load-test user",
                    "baseline": await asyncio.to_thread(service.metrics),
                },
            )
            start = time.monotonic()
            count = 0
            warm = None
            while time.monotonic() - start < options.seconds:
                remaining = options.seconds - (time.monotonic() - start)
                result = await cycle(
                    service,
                    streams=options.streams,
                    seconds=min(options.cycle_seconds, max(0.1, remaining)),
                )
                count += 1
                if warm is None:
                    warm = result["settled"]
                for pid, current in result["settled"].items():
                    assert current["threads"] <= warm[pid]["threads"] + 4
                    # Pool growth can retain up to pool_size DB descriptors.
                    assert current["fds"] <= warm[pid]["fds"] + service.pool_size + 4
                emit(destination, {"type": "cycle", "cycle": count, **result})
                print(
                    json.dumps(
                        {
                            "cycle": count,
                            "elapsed_seconds": round(time.monotonic() - start, 1),
                            "closed_streams": count * options.streams,
                            "settled": result["settled"],
                        }
                    ),
                    flush=True,
                )
            measured_seconds = time.monotonic() - start
            shutdown = await asyncio.to_thread(service.stop_server)
            records = service.records("closed")
            assert len(records) == service.workers
            assert (
                sum(record["closed"] for record in records) == count * options.streams
            )
            assert all(record["opened"] == record["closed"] for record in records)
            assert all(
                record["background_done"] and record["client_closed"]
                for record in records
            )
        # This footer is emitted only after container/database cleanup too.
        emit(
            destination,
            {
                "type": "complete",
                "cycles": count,
                "measured_seconds": measured_seconds,
                "shutdown_seconds": shutdown,
                "workers": records,
                "test_resources_removed": True,
            },
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--cycle-seconds", type=float, default=10)
    parser.add_argument("--streams", type=int, default=16)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    if not 1 <= options.seconds <= 86400 or not 1 <= options.cycle_seconds <= 60:
        parser.error("seconds must be 1..86400 and cycle-seconds 1..60")
    if not 2 <= options.streams <= 32:
        parser.error("streams must be 2..32 for the disposable PostgreSQL profile")
    if options.output.exists():
        parser.error("output already exists; choose a new report path")
    asyncio.run(run(options))


if __name__ == "__main__":
    main()
