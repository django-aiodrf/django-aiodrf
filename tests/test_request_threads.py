"""``AIODRF["REQUEST_THREADS"]``: request threads kept for reuse (opt-in)."""

import asyncio
import json
import threading
from contextlib import asynccontextmanager

import pytest
from asgiref.sync import sync_to_async
from django.http import JsonResponse
from django.test import override_settings
from django.urls import path

from aiodrf.asgi import get_asgi_application
from aiodrf.settings import setting_error
from tests.asgi_driver import http_scope

pytestmark = pytest.mark.django_db(transaction=True)

blocked = threading.Event()
release = threading.Event()


def _thread_name():
    return threading.current_thread().name


def _wait_for_release():
    blocked.set()
    release.wait(5)
    return _thread_name()


async def names(request):
    # Two thread-sensitive hops of one request.
    first = await sync_to_async(_thread_name)()
    second = await sync_to_async(_thread_name)()
    return JsonResponse({"threads": [first, second]})


barrier = threading.Barrier(2, timeout=5)


def _meet():
    barrier.wait()
    return _thread_name()


async def meet(request):
    return JsonResponse({"thread": await sync_to_async(_meet)()})


async def stuck(request):
    return JsonResponse({"thread": await sync_to_async(_wait_for_release)()})


late = []
tasks = set()


async def leave_behind(request):
    async def later():
        await asyncio.sleep(0.05)
        late.append(await sync_to_async(_thread_name)())

    # Started, not awaited: it runs after the request ended.
    task = asyncio.get_running_loop().create_task(later())
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return JsonResponse({"thread": await sync_to_async(_thread_name)()})


urlpatterns = [
    path("names/", names),
    path("leave-behind/", leave_behind),
    path("meet/", meet),
    path("stuck/", stuck),
]

reuse = override_settings(
    ROOT_URLCONF=__name__, AIODRF={"REQUEST_THREADS": 2}, FASTDRF={}
)


async def call(application, url, *, disconnect=None, state=None):
    """One request through the ASGI application; the JSON answer, or None."""
    sent = []
    first = True

    async def receive():
        nonlocal first
        if first:
            first = False
            return {"type": "http.request", "body": b"", "more_body": False}
        # Later reads wait for the client to go, or forever.
        await (disconnect or asyncio.Event()).wait()
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    await application(http_scope(url, state=state), receive, send)
    if not sent:
        return None
    return json.loads(sent[1]["body"])


@reuse
async def test_lifespan_shutdown_joins_request_threads():
    from tests.asgi_driver import ASGIDriver

    application = get_asgi_application()
    state = {}
    scope = {"type": "lifespan", "state": state}
    async with ASGIDriver(application, scope) as driver:
        await driver.incoming.put({"type": "lifespan.startup"})
        assert (await driver.receive())["type"] == "lifespan.startup.complete"
        names = (await call(application, "/names/", state=dict(state)))["threads"]
        threads = [thread for thread in threading.enumerate() if thread.name in names]
        assert threads
        await driver.incoming.put({"type": "lifespan.shutdown"})
        assert (await driver.receive())["type"] == "lifespan.shutdown.complete"
        await driver.finish()
        assert all(not thread.is_alive() for thread in threads)


@asynccontextmanager
async def running_application():
    from tests.asgi_driver import ASGIDriver

    wrapped = get_asgi_application()
    state = {}
    async with ASGIDriver(wrapped, {"type": "lifespan", "state": state}) as driver:
        await driver.incoming.put({"type": "lifespan.startup"})
        assert (await driver.receive())["type"] == "lifespan.startup.complete"

        async def application(scope, receive, send):
            await wrapped({**scope, "state": dict(state)}, receive, send)

        application.pool = state["aiodrf.request_threads"]
        try:
            yield application
        finally:
            await driver.incoming.put({"type": "lifespan.shutdown"})
            assert (await driver.receive())["type"] == "lifespan.shutdown.complete"
            await driver.finish()


@reuse
async def test_one_request_uses_one_thread_and_the_next_reuses_it():
    async with running_application() as application:
        first = (await call(application, "/names/"))["threads"]
        second = (await call(application, "/names/"))["threads"]
        assert first[0] == first[1]
        assert second == first


@pytest.mark.aiodrf_settings(REQUEST_THREADS=None)
@override_settings(ROOT_URLCONF=__name__)
async def test_by_default_each_request_gets_djangos_own_thread():
    application = get_asgi_application()
    first = (await call(application, "/names/"))["threads"]
    second = (await call(application, "/names/"))["threads"]
    assert first[0] == first[1]
    assert second[0] != first[0]


@reuse
async def test_concurrent_requests_have_threads_of_their_own():
    async with running_application() as application:
        # Both wait for the other in a thread: one shared thread would deadlock.
        answers = await asyncio.wait_for(
            asyncio.gather(call(application, "/meet/"), call(application, "/meet/")), 10
        )
        assert answers[0]["thread"] != answers[1]["thread"]


@reuse
async def test_a_thread_busy_with_an_aborted_request_is_not_lent():
    async with running_application() as application:
        reused = (await call(application, "/names/"))["threads"][0]
        blocked.clear()
        release.clear()
        disconnect = asyncio.Event()
        aborted = asyncio.create_task(
            call(application, "/stuck/", disconnect=disconnect)
        )
        await asyncio.to_thread(blocked.wait, 5)
        disconnect.set()
        # asgiref lets the synchronous code finish before the request ends; the
        # thread is not lent meanwhile.
        other = (await asyncio.wait_for(call(application, "/names/"), 5))["threads"][0]
        assert other != reused
        assert not aborted.done()
        release.set()
        assert await asyncio.wait_for(aborted, 5) is None
        lent = {(await call(application, "/names/"))["threads"][0] for _ in range(3)}
        assert lent <= {reused, other}


@reuse
async def test_code_a_request_left_running_does_not_use_a_lent_thread():
    async with running_application() as application:
        late.clear()
        left = (await call(application, "/leave-behind/"))["thread"]
        lent = (await call(application, "/names/"))["threads"][0]
        assert lent == left
        await asyncio.sleep(0.2)
        assert late
        assert late[0] != lent


@override_settings(ROOT_URLCONF=__name__, AIODRF={"REQUEST_THREADS": 1}, FASTDRF={})
async def test_idle_threads_beyond_the_setting_end():
    async with running_application() as application:
        await asyncio.gather(*(call(application, "/names/") for _ in range(4)))
        await asyncio.sleep(0.2)
        assert application.pool.idle_count() <= 1


async def test_other_scopes_are_refused_as_django_does():
    with override_settings(AIODRF={"REQUEST_THREADS": 2}, FASTDRF={}):
        application = get_asgi_application()
    with pytest.raises(ValueError, match="Django can only handle ASGI/HTTP"):
        await application.application({"type": "websocket"}, None, None)


@pytest.mark.parametrize("value", [0, -1, 1.5, "4", True])
def test_the_setting_is_a_positive_integer_or_none(value):
    assert setting_error("REQUEST_THREADS", value)
    assert setting_error("REQUEST_THREADS", None) is None
    assert setting_error("REQUEST_THREADS", 8) is None


async def test_pool_close_waits_for_busy_worker_despite_repeated_cancellation():
    from aiodrf.asgi import _RequestThreads

    pool = _RequestThreads(1)
    executor = pool.lend()
    entered = threading.Event()
    finish = threading.Event()

    def work():
        entered.set()
        assert finish.wait(5)

    future = executor.submit(work)
    assert await asyncio.to_thread(entered.wait, 5)
    pool.give_back(executor)
    closing = asyncio.create_task(pool.aclose())
    try:
        await asyncio.sleep(0)
        closing.cancel()
        await asyncio.sleep(0)
        closing.cancel()
        await asyncio.sleep(0)
        assert not closing.done()
    finally:
        finish.set()
    with pytest.raises(asyncio.CancelledError):
        await closing
    assert future.done()
    assert not any(thread.is_alive() for thread in executor._threads)
    assert pool.idle_count() == 0
    with pytest.raises(RuntimeError, match="closed"):
        pool.lend()


@reuse
async def test_repeated_lifespan_startups_get_independent_thread_pools():
    from tests.asgi_driver import ASGIDriver

    application = get_asgi_application()
    pools = []
    for _ in range(2):
        state = {}
        async with ASGIDriver(
            application, {"type": "lifespan", "state": state}
        ) as driver:
            await driver.incoming.put({"type": "lifespan.startup"})
            assert (await driver.receive())["type"] == "lifespan.startup.complete"
            pools.append(state["aiodrf.request_threads"])
            await call(application, "/names/", state=dict(state))
            await driver.incoming.put({"type": "lifespan.shutdown"})
            assert (await driver.receive())["type"] == "lifespan.shutdown.complete"
            await driver.finish()
        assert "aiodrf.request_threads" not in state
    assert pools[0] is not pools[1]
    assert all(pool._closed for pool in pools)
