"""SSE readers and worker discovery shared by tests and the soak CLI."""

import asyncio
import contextlib
import json


async def first_event(client, stack, url):
    response = await stack.enter_async_context(client.stream("GET", url))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    iterator = response.aiter_lines()
    await stack.enter_async_context(contextlib.aclosing(iterator))
    async with asyncio.timeout(10):
        async for line in iterator:
            if line.startswith("data:"):
                data = json.loads(line.removeprefix("data:"))
                assert data["ready"] is True
                return data["pid"], iterator
    raise AssertionError("SSE ended before its first event")


async def worker_snapshots(client, service):
    result = {}
    async with asyncio.timeout(10):
        while result.keys() != service.active_pids():
            # New connections exercise the shared listening socket, not a
            # client keepalive connection permanently assigned to one worker.
            response = await client.get(
                service.url + "/metrics/", headers={"Connection": "close"}
            )
            assert response.status_code == 200, response.text
            values = response.json()
            result[values["pid"]] = values
            await asyncio.sleep(0.01)
    return result
