"""
Code a project wrote runs in a worker unless aiodrf knows it may run on the
event loop, one callable at a time: a throttle's ``wait()`` after an async
``aallow_request()``, the request class's constructor, a synchronous wrapper
around an async handler. What aiodrf or the project declared stays inline.
``async_unsafe`` stands for a project's database, cache or HTTP work.
"""

import asyncio
import functools
import inspect
import threading

import pytest
from asgiref.sync import markcoroutinefunction, sync_to_async
from django.core.cache import cache
from django.utils.asyncio import async_unsafe
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import exceptions
from rest_framework import negotiation as drf_negotiation
from rest_framework import permissions as drf_permissions
from rest_framework import throttling as drf_throttling

from aiodrf import utils
from aiodrf.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
    throttle_classes,
)
from aiodrf.request import Request
from aiodrf.response import Response
from aiodrf.test import AsyncAPIRequestFactory, count_hops
from aiodrf.throttling import AnonFixedWindowRateThrottle
from aiodrf.utils import async_safe, register_pure_method
from aiodrf.views import APIView

factory = AsyncAPIRequestFactory()


class Endpoint(APIView):
    authentication_classes = []
    permission_classes = []
    throttle_classes = []

    async def get(self, request):
        return Response({"ok": True})


async def call(view_class, **headers):
    with count_hops() as hops:
        response = await view_class.as_view()(factory.get("/", headers=headers))
    # Classes defined in a test are named after it: ``test_x.<locals>.View.get``.
    return response, [name.rpartition(".<locals>.")[2] for name in hops.calls]


# -- A throttle's wait() ---------------------------------------------------------------

calls: list[str] = []


def throttle(name, allowed, duration, *, async_allow=True, pure_wait=False):
    """A throttle that records what the view asks it, in the order asked."""

    def allow_request(self, request, view):
        calls.append(f"{name}.allow")
        return allowed

    async def aallow_request(self, request, view):
        return allow_request(self, request, view)

    def wait(self):
        calls.append(f"{name}.wait")
        return duration

    attrs = (
        {"aallow_request": aallow_request}
        if async_allow
        else {"allow_request": allow_request}
    )
    attrs["wait"] = (
        async_safe(wait) if pure_wait else async_unsafe(f"{name}.wait ran")(wait)
    )
    return type(name, (drf_throttling.BaseThrottle,), attrs)


def throttled_view(*throttle_classes):
    return type("Throttled", (Endpoint,), {"throttle_classes": list(throttle_classes)})


async def test_the_wait_of_an_async_throttle_runs_in_a_worker():
    response, hops = await call(throttled_view(throttle("Async", False, 2)))
    assert response.status_code == 429
    assert response["Retry-After"] == "2"
    assert hops == ["wait"]


async def test_every_denying_throttle_is_asked_in_drfs_order():
    calls.clear()
    view = throttled_view(
        throttle("First", False, 5),
        throttle("Allowing", True, 99, async_allow=False),
        throttle("Pure", False, 7, pure_wait=True),
        throttle("Unknown", False, None),
        throttle("Last", False, 3, async_allow=False),
    )
    response, _ = await call(view)
    assert response.status_code == 429
    # The longest wait; None (a rate changed meanwhile, DRF #1438) is left out.
    assert response["Retry-After"] == "7"
    assert calls == [
        "First.allow",
        "First.wait",
        "Allowing.allow",
        "Pure.allow",
        "Pure.wait",
        "Unknown.allow",
        "Unknown.wait",
        "Last.allow",
        "Last.wait",
    ]


async def test_a_wait_declared_pure_or_drfs_own_runs_inline():
    class Denying(drf_throttling.BaseThrottle):
        async def aallow_request(self, request, view):
            return False

    for view, retry_after in (
        (throttled_view(throttle("Pure", False, 4, pure_wait=True)), "4"),
        # BaseThrottle.wait(): None, so no Retry-After.
        (throttled_view(Denying), None),
    ):
        response, hops = await call(view)
        assert response.status_code == 429
        assert response.get("Retry-After") == retry_after
        assert hops == []


async def test_only_none_durations_give_no_retry_after():
    response, _ = await call(
        throttled_view(throttle("A", False, None), throttle("B", False, None))
    )
    assert response.status_code == 429
    assert not response.has_header("Retry-After")


async def test_a_throttle_declared_pure_does_not_cover_its_wait():
    class Declared(drf_throttling.BaseThrottle):
        def allow_request(self, request, view):
            return False

        @async_unsafe("wait ran on the event loop")
        def wait(self):
            return 2

    register_pure_method(Declared, "allow_request")
    response, hops = await call(throttled_view(Declared))
    assert response.status_code == 429
    assert response["Retry-After"] == "2"
    assert hops == ["Declared.wait"]


class NoRequests(drf_throttling.AnonRateThrottle):
    rate = "0/min"


class NoRequestsFixed(AnonFixedWindowRateThrottle):
    rate = "0/min"


@pytest.mark.parametrize("throttle_class", [NoRequests, NoRequestsFixed])
async def test_a_rate_throttle_with_an_in_process_cache_denies_without_a_hop(
    throttle_class,
):
    cache.clear()
    response, hops = await call(throttled_view(throttle_class))
    assert response.status_code == 429
    assert 0 < int(response["Retry-After"]) <= 61
    assert hops == []


async def test_a_throttle_with_an_allocator_of_its_own_is_built_in_a_worker():
    class Allocated(drf_throttling.AnonRateThrottle):
        rate = "5/min"

        @async_unsafe("throttle.__new__ ran on the event loop")
        def __new__(cls, *args, **kwargs):
            return super().__new__(cls)

    cache.clear()
    response, hops = await call(throttled_view(Allocated))
    assert response.status_code == 200
    assert hops == ["durations_of_failed"]


async def test_a_negotiator_overriding_what_select_renderer_calls_runs_in_a_worker():
    class Filtering(drf_negotiation.DefaultContentNegotiation):
        @async_unsafe("filter_renderers ran on the event loop")
        def filter_renderers(self, renderers, format):
            return super().filter_renderers(renderers, format)

        @async_unsafe("get_accept_list ran on the event loop")
        def get_accept_list(self, request):
            return super().get_accept_list(request)

    view = type("Negotiated", (Endpoint,), {"content_negotiation_class": Filtering})
    for query in ("/", "/?format=json"):
        with count_hops() as hops:
            response = await view.as_view()(factory.get(query))
        assert response.status_code == 200
        assert hops.calls


# -- The request class --------------------------------------------------------------


def with_request_class(request_class):
    return type("Built", (Endpoint,), {"request_class": request_class})


async def test_a_request_class_with_a_constructor_is_built_in_a_worker():
    class Initialized(Request):
        @async_unsafe("Request.__init__ ran on the event loop")
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)

    class Allocated(Request):
        @async_unsafe("Request.__new__ ran on the event loop")
        def __new__(cls, *args, **kwargs):
            return super().__new__(cls)

    for request_class in (Initialized, Allocated):
        response, hops = await call(with_request_class(request_class))
        assert response.status_code == 200
        assert hops == ["APIView.initialize_request"]


async def test_a_request_class_adding_data_or_declared_pure_is_built_inline():
    class Tagged(Request):
        tag = "v2"

    @async_safe
    class Declared(Request):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)

    class Registered(Request):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)

    register_pure_method(Registered, "__init__")
    try:
        for request_class in (Tagged, Declared, Registered):
            response, hops = await call(with_request_class(request_class))
            assert response.status_code == 200
            assert hops == []
    finally:
        utils._pure.methods.pop(Registered, None)
        utils._pure.changed()
    response, hops = await call(with_request_class(Registered))
    assert hops == ["APIView.initialize_request"]


async def test_an_initialize_request_of_its_own_runs_in_a_worker():
    class Own(Endpoint):
        @async_unsafe("initialize_request ran on the event loop")
        def initialize_request(self, request, *args, **kwargs):
            return super().initialize_request(request, *args, **kwargs)

    response, hops = await call(Own)
    assert response.status_code == 200
    assert hops == ["Own.initialize_request"]


# -- Synchronous wrappers around async handlers ------------------------------------


def blocking(function):
    """A decorator whose synchronous prologue does I/O, then returns the coroutine."""

    @functools.wraps(function)
    @async_unsafe("the wrapper ran on the event loop")
    def wrapper(*args, **kwargs):
        return function(*args, **kwargs)

    return wrapper


def on_loop():
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


class Wrapped(Endpoint):
    @blocking
    async def get(self, request):
        assert on_loop()
        return Response({"ok": True})


async def test_a_sync_wrapper_around_an_async_handler_runs_in_a_worker():
    response, hops = await call(Wrapped)
    assert response.status_code == 200
    assert response.data == {"ok": True}
    assert hops == ["Wrapped.get"]


async def test_a_partial_of_a_wrapper_runs_in_a_worker_and_of_a_coroutine_inline():
    async def handler(view, request):
        assert on_loop()
        return Response({"view": type(view).__name__})

    for function, expected in ((handler, []), (blocking(handler), ["partial"])):

        class Partial(Endpoint):
            handler = staticmethod(function)

            def setup(self, request, *args, **kwargs):
                super().setup(request, *args, **kwargs)
                self.get = functools.partial(self.handler, self)

        response, hops = await call(Partial)
        assert response.data == {"view": "Partial"}
        assert hops == expected


@pytest.mark.parametrize("mark", [markcoroutinefunction, inspect.markcoroutinefunction])
async def test_a_wrapper_marked_as_a_coroutine_function_is_called_inline(mark):
    def marked(function):
        @functools.wraps(function)
        def wrapper(*args, **kwargs):
            assert on_loop()
            return function(*args, **kwargs)

        return mark(wrapper)

    class Marked(Endpoint):
        @marked
        async def get(self, request):
            return Response({"ok": True})

    response, hops = await call(Marked)
    assert response.data == {"ok": True}
    assert hops == []


class Listed(Endpoint):
    async def get(self, request):
        assert on_loop()
        return Response({"ok": True})


@extend_schema_view(get=extend_schema(description="Documented"))
class Documented(Listed):
    """drf-spectacular wraps the inherited ``get`` to isolate its schema."""


async def test_drf_spectaculars_wrapper_is_called_inline():
    assert Documented.get is not Listed.get
    response, hops = await call(Documented)
    assert response.data == {"ok": True}
    assert hops == []


async def test_a_wrapper_that_raises_is_handled_as_drf_handles_the_handler():
    def refusing(function):
        @functools.wraps(function)
        @async_unsafe("the wrapper ran on the event loop")
        def wrapper(*args, **kwargs):
            raise exceptions.NotFound

        return wrapper

    class Refused(Endpoint):
        @refusing
        async def get(self, request):
            raise AssertionError("not reached")

    response, _ = await call(Refused)
    assert response.status_code == 404


async def test_cancelling_during_the_wrapper_never_starts_the_handler():
    entered, release = threading.Event(), threading.Event()
    started = []

    def slow(function):
        @functools.wraps(function)
        def wrapper(*args, **kwargs):
            entered.set()
            release.wait(5)
            return function(*args, **kwargs)

        return wrapper

    class Slow(Endpoint):
        @slow
        async def get(self, request):
            started.append(True)
            return Response({})

    task = asyncio.ensure_future(Slow.as_view()(factory.get("/")))
    assert await asyncio.to_thread(entered.wait, 5)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert started == []


async def test_a_sync_wrapper_around_an_async_pair_member_runs_in_a_worker():
    class Initial(Endpoint):
        permission_classes = [drf_permissions.AllowAny]

        @blocking
        async def initial(self, request, *args, **kwargs):
            assert on_loop()
            request.checked = True

        async def get(self, request):
            return Response({"checked": request.checked})

    response, hops = await call(Initial)
    assert response.data == {"checked": True}
    assert hops == ["Initial.initial"]


async def test_a_sync_wrapper_around_an_async_exception_handler_runs_in_a_worker(
    settings,
):
    settings.REST_FRAMEWORK = {
        **settings.REST_FRAMEWORK,
        "EXCEPTION_HANDLER": f"{__name__}.wrapped_exception_handler",
    }

    class Failing(Endpoint):
        async def get(self, request):
            raise exceptions.NotFound

    response, hops = await call(Failing)
    assert response.status_code == 404
    assert response.data == {"handled": True}
    assert hops == ["wrapped_exception_handler"]


@blocking
async def wrapped_exception_handler(exc, context):
    assert on_loop()
    return Response({"handled": True}, status=exc.status_code)


class Checked:
    @blocking
    async def check(self):
        assert on_loop()
        return "checked"


async def test_the_sync_bridge_calls_a_wrapper_in_its_own_thread():
    # DRF's synchronous callers (schema generation, an ``initial()`` override).
    assert utils.resolve_pair(Checked, "check", "acheck") is utils.Impl.SYNC_IS_ASYNC
    assert (
        await sync_to_async(utils.call_pair_sync)(Checked(), "check", "acheck")
        == "checked"
    )
    assert await utils.call_pair(Checked(), "check", "acheck") == "checked"


# -- @api_view ---------------------------------------------------------------------


def function_view(function):
    function = authentication_classes([])(
        permission_classes([])(throttle_classes([])(function))
    )
    return api_view(["GET"])(function)


async def call_function(view):
    with count_hops() as hops:
        response = await view(factory.get("/"))
    return response, hops.calls


async def test_a_sync_wrapper_around_an_async_function_view_runs_in_a_worker():
    @blocking
    async def wrapped(request):
        assert on_loop()
        return Response({"ok": True})

    response, hops = await call_function(function_view(wrapped))
    assert response.data == {"ok": True}
    assert len(hops) == 1


@pytest.mark.parametrize(
    "mark", [None, markcoroutinefunction, inspect.markcoroutinefunction]
)
async def test_an_async_or_marked_function_view_is_called_inline(mark):
    async def handler(request):
        assert on_loop()
        return Response({"ok": True})

    def wrapper(request):
        assert on_loop()
        return handler(request)

    response, hops = await call_function(
        function_view(mark(wrapper) if mark else handler)
    )
    assert response.data == {"ok": True}
    assert hops == []


async def test_a_sync_function_view_runs_in_a_worker():
    def handler(request):
        assert not on_loop()
        return Response({"ok": True})

    response, hops = await call_function(function_view(handler))
    assert response.data == {"ok": True}
    assert len(hops) == 1
