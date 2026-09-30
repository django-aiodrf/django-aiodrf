"""``AIODRF["REQUEST_THREADS"]``: request threads kept for reuse (opt-in)."""

import asyncio
import json
import threading

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

reuse = override_settings(ROOT_URLCONF=__name__, AIODRF={"REQUEST_THREADS": 2})


async def call(application, url, *, disconnect=None):
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

    await application(http_scope(url), receive, send)
    if not sent:
        return None
    return json.loads(sent[1]["body"])


@reuse
async def test_one_request_uses_one_thread_and_the_next_reuses_it():
    application = get_asgi_application()
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
    application = get_asgi_application()
    # Both wait for the other in a thread: one shared thread would deadlock.
    answers = await asyncio.wait_for(
        asyncio.gather(call(application, "/meet/"), call(application, "/meet/")), 10
    )
    assert answers[0]["thread"] != answers[1]["thread"]


@reuse
async def test_a_thread_busy_with_an_aborted_request_is_not_lent():
    application = get_asgi_application()
    reused = (await call(application, "/names/"))["threads"][0]
    blocked.clear()
    release.clear()
    disconnect = asyncio.Event()
    aborted = asyncio.create_task(call(application, "/stuck/", disconnect=disconnect))
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
    application = get_asgi_application()
    late.clear()
    left = (await call(application, "/leave-behind/"))["thread"]
    lent = (await call(application, "/names/"))["threads"][0]
    assert lent == left
    await asyncio.sleep(0.2)
    assert late
    assert late[0] != lent


@override_settings(ROOT_URLCONF=__name__, AIODRF={"REQUEST_THREADS": 1})
async def test_idle_threads_beyond_the_setting_end():
    application = get_asgi_application()
    await asyncio.gather(*(call(application, "/names/") for _ in range(4)))
    await asyncio.sleep(0.2)
    assert application.application.request_threads.idle_count() <= 1


async def test_other_scopes_are_refused_as_django_does():
    with override_settings(AIODRF={"REQUEST_THREADS": 2}):
        application = get_asgi_application()
    with pytest.raises(ValueError, match="Django can only handle ASGI/HTTP"):
        await application.application({"type": "websocket"}, None, None)


@pytest.mark.parametrize("value", [0, -1, 1.5, "4", True])
def test_the_setting_is_a_positive_integer_or_none(value):
    assert setting_error("REQUEST_THREADS", value)
    assert setting_error("REQUEST_THREADS", None) is None
    assert setting_error("REQUEST_THREADS", 8) is None
