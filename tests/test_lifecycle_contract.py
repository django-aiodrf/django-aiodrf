"""Lifespan failure, loop isolation and streaming resource ownership."""

import asyncio
import contextlib
import threading

import pytest
from aiodrf_asgi_lifespan.asgi import LifespanApplication
from aiodrf_asgi_lifespan.signals import asgi_shutdown, asgi_startup
from django.utils.asyncio import async_unsafe

from aiodrf.response import (
    EventStreamResponse,
    StreamingArrayResponse,
    StreamingResponse,
)
from tests.asgi_driver import ASGIDriver


@contextlib.contextmanager
def connected(signal, *receivers):
    for receiver in receivers:
        signal.connect(receiver, weak=False)
    try:
        yield
    finally:
        for receiver in receivers:
            signal.disconnect(receiver)


async def test_startup_failure_waits_for_other_receivers_to_release_partial_resources():
    acquired, release = asyncio.Event(), asyncio.Event()
    closed = []

    async def resource(sender, **kwargs):
        try:
            acquired.set()
            await release.wait()
        finally:
            closed.append(True)

    async def failing(sender, **kwargs):
        await acquired.wait()
        raise RuntimeError("startup refused")

    with connected(asgi_startup, resource, failing):
        async with ASGIDriver(
            LifespanApplication(None), {"type": "lifespan"}
        ) as driver:
            await driver.incoming.put({"type": "lifespan.startup"})
            await asyncio.wait_for(acquired.wait(), 3)
            release.set()
            assert (await driver.receive())["type"] == "lifespan.startup.failed"
            await driver.finish()
            assert closed == [True]


@pytest.mark.parametrize("cancel", [False, True])
async def test_shutdown_failure_and_cancellation_close_receiver_resources(cancel):
    entered = asyncio.Event()
    closed = []

    async def shutdown(sender, **kwargs):
        try:
            entered.set()
            if cancel:
                await asyncio.Event().wait()
            raise RuntimeError("shutdown refused")
        finally:
            closed.append(True)

    with connected(asgi_shutdown, shutdown):
        async with ASGIDriver(
            LifespanApplication(None), {"type": "lifespan"}
        ) as driver:
            await driver.incoming.put({"type": "lifespan.shutdown"})
            await asyncio.wait_for(entered.wait(), 3)
            if cancel:
                driver.task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await driver.finish()
            else:
                assert (await driver.receive())["type"] == "lifespan.shutdown.failed"
                await driver.finish()
            assert closed == [True]


def test_lifespan_instances_keep_resources_on_their_own_loops():
    observed = []

    @async_unsafe("sync receiver on loop")
    def sync_receiver(sender, **kwargs):
        observed.append(("worker", threading.get_ident()))

    async def startup(sender, scope, **kwargs):
        if "state" in scope:
            scope["state"]["library"] = {"loop": asyncio.get_running_loop()}

    async def shutdown(sender, scope, **kwargs):
        if "state" in scope:
            assert scope["state"]["library"].pop("loop") is asyncio.get_running_loop()
            scope["state"]["library"]["closed"] = True

    async def run(state):
        scope = {"type": "lifespan"}
        if state is not None:
            scope["state"] = state
        async with ASGIDriver(LifespanApplication(None), scope) as driver:
            await driver.incoming.put({"type": "lifespan.startup"})
            assert (await driver.receive())["type"] == "lifespan.startup.complete"
            await driver.incoming.put({"type": "lifespan.shutdown"})
            assert (await driver.receive())["type"] == "lifespan.shutdown.complete"
            await driver.finish()

    states = [{"other-wrapper": 1}, {"other-wrapper": 2}, None]
    with (
        connected(asgi_startup, startup, sync_receiver),
        connected(asgi_shutdown, shutdown),
    ):
        for state in states:
            asyncio.run(run(state))
    assert states[:2] == [
        {"other-wrapper": 1, "library": {"closed": True}},
        {"other-wrapper": 2, "library": {"closed": True}},
    ]
    assert all(thread != threading.get_ident() for _, thread in observed)


@pytest.mark.parametrize("response_class", [StreamingResponse, StreamingArrayResponse])
@pytest.mark.parametrize("failure", ["producer", "renderer"])
async def test_stream_failures_close_once_and_do_not_fake_a_complete_body(
    response_class, failure
):
    from rest_framework.renderers import JSONRenderer

    closed = []

    class Renderer(JSONRenderer):
        def render(self, value, *args, **kwargs):
            if failure == "renderer" and value == 2:
                raise ValueError("render refused")
            return super().render(value, *args, **kwargs)

    async def items():
        try:
            yield 1
            if failure == "producer":
                raise ValueError("producer refused")
            yield 2
        finally:
            closed.append(True)

    response = response_class(items(), renderer=Renderer())
    chunks = []

    async def consume():
        async for chunk in response:
            chunks.append(chunk)  # noqa: PERF401 -- retain chunks yielded before failure

    with pytest.raises(ValueError, match="refused"):
        await consume()
    await response.aclose()
    assert closed == [True]
    if response_class is StreamingArrayResponse:
        assert b"".join(chunks) == b"[1"  # Headers/body cannot be retracted.


async def test_cancellation_during_stream_cleanup_propagates_without_orphan_tasks():
    cleaning = asyncio.Event()
    closed = []

    async def events():
        try:
            yield "one"
        finally:
            try:
                cleaning.set()
                await asyncio.Event().wait()
            finally:
                closed.append(True)

    response = EventStreamResponse(events())
    iterator = aiter(response)
    await anext(iterator)
    closing = asyncio.create_task(iterator.aclose())
    await asyncio.wait_for(cleaning.wait(), 3)
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    assert closed == [True]
