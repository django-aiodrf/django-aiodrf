"""Managed ASGI resources, configuration and request-state ownership."""

import asyncio
import gc
import threading
import weakref
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
from django.urls import path

from aiodrf.asgi import LifespanApplication, get_asgi_application, get_lifespan_state
from aiodrf.checks import check_settings
from aiodrf.response import Response
from aiodrf.settings import aiodrf_settings
from aiodrf.signals import asgi_shutdown, asgi_startup
from aiodrf.views import APIView
from tests.asgi_driver import ASGIDriver, http_scope
from tests.test_lifecycle_contract import connected


@dataclass
class Resource:
    loop: object
    closed: bool = False


@asynccontextmanager
async def configured_lifespan():
    resource = Resource(asyncio.get_running_loop())
    try:
        yield resource
    finally:
        resource.closed = True


class StateView(APIView):
    authentication_classes = []
    permission_classes = []

    async def get(self, request):
        resource = get_lifespan_state(request, Resource)
        assert resource.loop is asyncio.get_running_loop()
        assert not resource.closed
        return Response({"live": True})


urlpatterns = [path("state/", StateView.as_view())]


async def start(driver):
    await driver.incoming.put({"type": "lifespan.startup"})
    return await driver.receive()


async def stop(driver):
    await driver.incoming.put({"type": "lifespan.shutdown"})
    message = await driver.receive()
    await driver.finish()
    return message


@pytest.mark.parametrize(
    "value", [configured_lifespan, f"{__name__}.configured_lifespan"]
)
def test_setting_resolves_factory_without_entering_it(value):
    with override_settings(AIODRF={"LIFESPAN": value}):
        assert check_settings(None) == []
        assert aiodrf_settings.LIFESPAN is configured_lifespan
        assert get_asgi_application().lifespan is configured_lifespan
        assert get_asgi_application(lifespan=None).lifespan is None
    assert aiodrf_settings.LIFESPAN is None


def test_checks_and_application_construction_do_not_call_factory():
    factory = Mock(side_effect=AssertionError("resource opened before startup"))
    with override_settings(AIODRF={"LIFESPAN": factory}):
        assert check_settings(None) == []
        application = get_asgi_application()
    assert application.lifespan is factory
    factory.assert_not_called()


@pytest.mark.parametrize(
    ("value", "error_type", "code"),
    [
        (False, ImproperlyConfigured, "aiodrf.E006"),
        ([], ImproperlyConfigured, "aiodrf.E006"),
        ("missing_lifespan.factory", ImportError, "aiodrf.E002"),
        ("builtins.False", ImproperlyConfigured, "aiodrf.E006"),
        ("builtins.None", ImproperlyConfigured, "aiodrf.E006"),
    ],
)
def test_invalid_setting_is_rejected_before_startup(value, error_type, code):
    with override_settings(AIODRF={"LIFESPAN": value}):
        assert [message.id for message in check_settings(None)] == [code]
        with pytest.raises(error_type, match="LIFESPAN"):
            getattr(aiodrf_settings, "LIFESPAN")  # noqa: B009 -- validates on access


def test_undecorated_async_factories_are_rejected_without_calling_them():
    async def coroutine():
        raise AssertionError("must not be called")

    async def generator():
        yield None

    class AsyncCallable:
        __call__ = staticmethod(coroutine)

    for factory in (coroutine, generator, AsyncCallable()):
        with override_settings(AIODRF={"LIFESPAN": factory}):
            assert [message.id for message in check_settings(None)] == ["aiodrf.E006"]
            with pytest.raises(ImproperlyConfigured, match="asynccontextmanager"):
                get_asgi_application()


@override_settings(ROOT_URLCONF=__name__, MIDDLEWARE=[])
async def test_typed_resource_reaches_real_django_and_drf_requests_while_live():
    application = get_asgi_application(lifespan=configured_lifespan)
    state = {"other-library": "untouched"}
    async with ASGIDriver(application, {"type": "lifespan", "state": state}) as driver:
        assert (await start(driver))["type"] == "lifespan.startup.complete"
        scope = http_scope("/state/", state=dict(state))
        from django.core.handlers.asgi import ASGIRequest

        request = ASGIRequest(scope, None)
        resource = get_lifespan_state(request, Resource)
        with pytest.raises(ImproperlyConfigured, match="must be str"):
            get_lifespan_state(request, str)
        async with ASGIDriver(application, scope) as http:
            await http.incoming.put({"type": "http.request", "body": b""})
            assert (await http.receive())["status"] == 200
            assert (await http.receive())["body"] == b'{"live":true}'
            await http.finish()
        assert not resource.closed
        assert (await stop(driver))["type"] == "lifespan.shutdown.complete"
    assert resource.closed
    assert state == {"other-library": "untouched"}
    # A stale shallow copy must not hand a closed client to another request.
    with pytest.raises(ImproperlyConfigured, match="No active"):
        get_lifespan_state(request, Resource)


@pytest.mark.parametrize("http_request", [SimpleNamespace(), SimpleNamespace(scope={})])
def test_state_access_without_lifespan_never_opens_resources(http_request):
    with pytest.raises(ImproperlyConfigured, match="No active"):
        get_lifespan_state(http_request, Resource)


@pytest.mark.parametrize("failure", [None, "startup", "shutdown"])
@pytest.mark.parametrize("suppress", [False, True])
async def test_context_encloses_existing_signals_and_unwinds_even_when_failure_is_suppressed(
    failure, suppress
):
    events = []

    @asynccontextmanager
    async def lifespan():
        events.append("enter")
        try:
            yield Resource(asyncio.get_running_loop())
        except RuntimeError:
            if not suppress:
                raise
        finally:
            events.append("exit")

    async def startup(sender, scope, **kwargs):
        assert sender is LifespanApplication
        assert get_lifespan_state(SimpleNamespace(scope=scope), Resource)
        events.append("startup")
        if failure == "startup":
            raise RuntimeError("startup refused")

    async def shutdown(sender, scope, **kwargs):
        assert get_lifespan_state(SimpleNamespace(scope=scope), Resource)
        events.append("shutdown")
        if failure == "shutdown":
            raise RuntimeError("shutdown refused")

    state = {}
    with connected(asgi_startup, startup), connected(asgi_shutdown, shutdown):
        async with ASGIDriver(
            LifespanApplication(None, lifespan=lifespan),
            {"type": "lifespan", "state": state},
        ) as driver:
            message = await start(driver)
            if failure == "startup":
                assert message["type"] == "lifespan.startup.failed"
                assert "startup refused" in message["message"]
                await driver.finish()
            else:
                assert message["type"] == "lifespan.startup.complete"
                message = await stop(driver)
                outcome = "failed" if failure else "complete"
                assert message["type"] == f"lifespan.shutdown.{outcome}"
                if failure:
                    assert "shutdown refused" in message["message"]
    assert state == {}
    assert events == (
        ["enter", "startup", "exit"]
        if failure == "startup"
        else ["enter", "startup", "shutdown", "exit"]
    )


@pytest.mark.parametrize("failure", ["enter", "exit"])
async def test_context_failure_reports_phase_and_closes_partially_opened_resources(
    failure,
):
    events = []

    @asynccontextmanager
    async def first():
        events.append("first enter")
        try:
            yield
        finally:
            events.append("first exit")

    @asynccontextmanager
    async def lifespan():
        async with first():
            if failure == "enter":
                raise RuntimeError("second resource refused")
            yield None
            raise RuntimeError("cleanup refused")

    async with ASGIDriver(
        LifespanApplication(None, lifespan=lifespan), {"type": "lifespan"}
    ) as driver:
        message = await start(driver)
        if failure == "enter":
            assert message["type"] == "lifespan.startup.failed"
            assert "second resource refused" in message["message"]
            await driver.finish()
        else:
            assert message["type"] == "lifespan.startup.complete"
            message = await stop(driver)
            assert message["type"] == "lifespan.shutdown.failed"
            assert "cleanup refused" in message["message"]
    assert events == ["first enter", "first exit"]


@pytest.mark.parametrize("state", [None, {"aiodrf.lifespan": "foreign"}])
async def test_unpublishable_state_fails_startup_and_closes_resource(state):
    resource = Resource(asyncio.get_running_loop())

    @asynccontextmanager
    async def lifespan():
        try:
            yield resource
        finally:
            resource.closed = True

    scope = {"type": "lifespan"}
    if state is not None:
        scope["state"] = state.copy()
    async with ASGIDriver(
        LifespanApplication(None, lifespan=lifespan), scope
    ) as driver:
        message = await start(driver)
        assert message["type"] == "lifespan.startup.failed"
        assert "scope['state']" in message["message"]
        await driver.finish()
    assert resource.closed
    assert scope.get("state") == state


async def test_none_yield_needs_no_server_state():
    events = []

    @asynccontextmanager
    async def lifespan():
        events.append("enter")
        try:
            yield None
        finally:
            events.append("exit")

    async with ASGIDriver(
        LifespanApplication(None, lifespan=lifespan), {"type": "lifespan"}
    ) as driver:
        assert (await start(driver))["type"] == "lifespan.startup.complete"
        assert (await stop(driver))["type"] == "lifespan.shutdown.complete"
    assert events == ["enter", "exit"]


@pytest.mark.parametrize("kind", ["none", "sync", "coroutine", "async_generator"])
async def test_invalid_returned_context_fails_without_unawaited_coroutine_warnings(
    kind,
):
    @contextmanager
    def sync_context():
        yield None

    async def coroutine():
        raise AssertionError("must not be awaited")

    async def generator():
        yield None

    factories = {
        "none": lambda: None,
        "sync": sync_context,
        "coroutine": lambda: coroutine(),  # noqa: PLW0108 -- deliberately a sync factory
        "async_generator": lambda: generator(),  # noqa: PLW0108
    }
    wrapper = LifespanApplication(None, lifespan=factories[kind])
    async with ASGIDriver(wrapper, {"type": "lifespan"}) as driver:
        message = await start(driver)
        assert message["type"] == "lifespan.startup.failed"
        assert "async context manager" in message["message"]
        await driver.finish()


@pytest.mark.parametrize(
    ("during_startup", "cleanup"),
    [
        (True, "ordinary"),
        (True, "suppresses"),
        (False, "ordinary"),
        (False, "suppresses"),
        (False, "raises"),
    ],
)
async def test_cancellation_unwinds_and_is_never_converted_to_success(
    during_startup, cleanup
):
    entered = asyncio.Event()
    events = []

    @asynccontextmanager
    async def lifespan():
        try:
            entered.set()
            if during_startup:
                await asyncio.Event().wait()
            yield None
        except asyncio.CancelledError:
            if during_startup or cleanup != "suppresses":
                raise
        finally:
            events.append("closed")
            if cleanup == "raises":
                raise ValueError("cleanup refused")

    async with ASGIDriver(
        LifespanApplication(None, lifespan=lifespan), {"type": "lifespan"}
    ) as driver:
        await driver.incoming.put({"type": "lifespan.startup"})
        await asyncio.wait_for(entered.wait(), 3)
        if not during_startup:
            assert (await driver.receive())["type"] == "lifespan.startup.complete"
        driver.task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await driver.finish()
        assert events == ["closed"]
        assert all(
            not message["type"].endswith("shutdown.complete") for message in driver.sent
        )


@pytest.mark.parametrize("first", ["lifespan.shutdown", "lifespan.startup"])
async def test_invalid_message_order_never_enters_twice(first):
    calls = []

    @asynccontextmanager
    async def lifespan():
        calls.append("enter")
        try:
            yield None
        finally:
            calls.append("exit")

    async with ASGIDriver(
        LifespanApplication(None, lifespan=lifespan), {"type": "lifespan"}
    ) as driver:
        await driver.incoming.put({"type": first})
        message = await driver.receive()
        if first == "lifespan.startup":
            assert message["type"] == "lifespan.startup.complete"
            message = await start(driver)
            assert message["type"] == "lifespan.shutdown.failed"
            assert calls == ["enter", "exit"]
        else:
            assert message["type"] == "lifespan.startup.failed"
            assert calls == []
        await driver.finish()


def test_same_application_on_concurrent_loops_never_shares_resources():
    wrapper = LifespanApplication(None, lifespan=configured_lifespan)
    barrier = threading.Barrier(2, timeout=5)

    async def run():
        state = {}
        async with ASGIDriver(wrapper, {"type": "lifespan", "state": state}) as driver:
            assert (await start(driver))["type"] == "lifespan.startup.complete"
            resource = get_lifespan_state(
                SimpleNamespace(scope={"state": dict(state)}), Resource
            )
            barrier.wait()  # Two independent event loops, not blocking production code.
            assert resource.loop is asyncio.get_running_loop()
            assert not resource.closed
            assert (await stop(driver))["type"] == "lifespan.shutdown.complete"
            return resource

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(lambda: asyncio.run(run())) for _ in range(2)]
        resources = [future.result(timeout=10) for future in futures]
    assert resources[0] is not resources[1]
    assert resources[0].loop is not resources[1].loop
    assert all(resource.closed for resource in resources)


async def test_settings_reload_does_not_replace_an_application_factory():
    with override_settings(AIODRF={"LIFESPAN": configured_lifespan}):
        application = get_asgi_application()
    assert aiodrf_settings.LIFESPAN is None
    state = {}
    async with ASGIDriver(application, {"type": "lifespan", "state": state}) as driver:
        assert (await start(driver))["type"] == "lifespan.startup.complete"
        resource = get_lifespan_state(SimpleNamespace(scope={"state": state}), Resource)
        assert (await stop(driver))["type"] == "lifespan.shutdown.complete"
    assert resource.closed


async def test_cleanup_failure_keeps_original_startup_error_in_traceback():
    @asynccontextmanager
    async def lifespan():
        try:
            yield None
        finally:
            raise ValueError("cleanup refused")

    async def startup(sender, **kwargs):
        raise RuntimeError("startup refused")

    with connected(asgi_startup, startup):
        async with ASGIDriver(
            LifespanApplication(None, lifespan=lifespan), {"type": "lifespan"}
        ) as driver:
            message = await start(driver)
            assert message["type"] == "lifespan.startup.failed"
            assert "startup refused" in message["message"]
            assert "cleanup refused" in message["message"]
            await driver.finish()


async def test_cancellation_during_cleanup_invalidates_all_request_copies():
    cleaning = asyncio.Event()
    closed = []

    @asynccontextmanager
    async def lifespan():
        try:
            yield Resource(asyncio.get_running_loop())
        finally:
            try:
                cleaning.set()
                await asyncio.Event().wait()
            finally:
                closed.append(True)

    state = {}
    async with ASGIDriver(
        LifespanApplication(None, lifespan=lifespan),
        {"type": "lifespan", "state": state},
    ) as driver:
        assert (await start(driver))["type"] == "lifespan.startup.complete"
        request_copy = SimpleNamespace(scope={"state": dict(state)})
        await driver.incoming.put({"type": "lifespan.shutdown"})
        await asyncio.wait_for(cleaning.wait(), 3)
        with pytest.raises(ImproperlyConfigured, match="No active"):
            get_lifespan_state(request_copy, Resource)
        driver.task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await driver.finish()
    assert closed == [True]
    assert state == {}


async def test_startup_send_failure_still_closes_context():
    events = []

    @asynccontextmanager
    async def lifespan():
        try:
            yield None
        finally:
            events.append("closed")

    async def receive():
        return {"type": "lifespan.startup"}

    async def send(message):
        events.append(message["type"])
        if message["type"] == "lifespan.startup.complete":
            raise OSError("server connection lost")

    await LifespanApplication(None, lifespan=lifespan)(
        {"type": "lifespan"}, receive, send
    )
    assert events == ["lifespan.startup.complete", "closed", "lifespan.startup.failed"]


@pytest.mark.parametrize("cleanup_fails", [False, True])
async def test_stale_request_scopes_do_not_retain_resources_after_shutdown(
    cleanup_fails,
):
    references = []

    @asynccontextmanager
    async def lifespan():
        resource = Resource(asyncio.get_running_loop())
        references.append(weakref.ref(resource))
        try:
            yield resource
        finally:
            resource.closed = True
            if cleanup_fails:
                raise RuntimeError("cleanup failed")

    state = {}
    async with ASGIDriver(
        LifespanApplication(None, lifespan=lifespan),
        {"type": "lifespan", "state": state},
    ) as driver:
        assert (await start(driver))["type"] == "lifespan.startup.complete"
        stale_request = SimpleNamespace(scope={"state": dict(state)})
        assert get_lifespan_state(stale_request, Resource) is references[0]()
        outcome = "failed" if cleanup_fails else "complete"
        assert (await stop(driver))["type"] == f"lifespan.shutdown.{outcome}"
    gc.collect()
    assert references[0]() is None
    with pytest.raises(ImproperlyConfigured, match="No active"):
        get_lifespan_state(stale_request, Resource)


# -- Test clients ------------------------------------------------------------------


class MutatingView(APIView):
    authentication_classes = []
    permission_classes = []

    async def get(self, request):
        # A request's copy of the state is its own, as with a server.
        seen = sorted(request.scope["state"])
        request.scope["state"]["scratch"] = True
        return Response({"keys": seen})


client_urls = override_settings(
    ROOT_URLCONF=(
        path("state/", StateView.as_view()),
        path("keys/", MutatingView.as_view()),
    )
)


@client_urls
async def test_a_test_client_reaches_the_lifespan_state():
    from aiodrf.test import AsyncAPIClient, lifespan

    async with lifespan(configured_lifespan) as state:
        client = AsyncAPIClient(lifespan=state)
        response = await client.get("/state/")
        assert response.status_code == 200, response.content
        assert (await client.get("/keys/")).data == {"keys": ["aiodrf.lifespan"]}
        assert (await client.get("/keys/")).data == {"keys": ["aiodrf.lifespan"]}
    # Closed with the block: a request made afterwards finds no live state.
    with pytest.raises(ImproperlyConfigured, match="No active aiodrf lifespan"):
        await client.get("/state/")


@client_urls
async def test_the_request_factory_carries_the_lifespan_state():
    from aiodrf.test import AsyncAPIRequestFactory, lifespan

    async with lifespan(configured_lifespan) as state:
        request = AsyncAPIRequestFactory(lifespan=state).get("/state/")
        response = await StateView.as_view()(request)
        assert response.status_code == 200


@client_urls
async def test_the_lifespan_defaults_to_the_setting():
    from aiodrf.test import AsyncAPIClient, lifespan

    with override_settings(AIODRF={"LIFESPAN": configured_lifespan}):
        async with lifespan() as state:
            response = await AsyncAPIClient(lifespan=state).get("/state/")
            assert response.status_code == 200


async def test_the_test_lifespan_sends_the_signals_around_the_resource():
    from aiodrf.test import lifespan

    events = []

    @asynccontextmanager
    async def factory():
        events.append("enter")
        yield Resource(asyncio.get_running_loop())
        events.append("exit")

    async def started(**kwargs):
        events.append("startup")

    async def stopped(**kwargs):
        events.append("shutdown")

    with connected(asgi_startup, started), connected(asgi_shutdown, stopped):
        async with lifespan(factory):
            events.append("test")
    assert events == ["enter", "startup", "test", "shutdown", "exit"]


async def test_a_failed_startup_raises_and_closes_what_was_opened():
    from aiodrf.test import lifespan

    events = []

    @asynccontextmanager
    async def factory():
        events.append("enter")
        try:
            yield Resource(asyncio.get_running_loop())
        finally:
            events.append("exit")

    async def refuse(**kwargs):
        raise ValueError("no database")

    with (
        connected(asgi_startup, refuse),
        pytest.raises(RuntimeError, match="ValueError: no database"),
    ):
        async with lifespan(factory):
            pytest.fail("the block must not run")
    assert events == ["enter", "exit"]


async def test_a_failing_test_still_shuts_the_lifespan_down():
    from aiodrf.test import lifespan

    events = []

    @asynccontextmanager
    async def factory():
        yield Resource(asyncio.get_running_loop())
        events.append("exit")

    with pytest.raises(AssertionError, match="the test failed"):
        async with lifespan(factory):
            raise AssertionError("the test failed")
    assert events == ["exit"]


@pytest.mark.parametrize("scope", [{"state": None}, {"state": {}}, {}, None])
def test_a_missing_state_names_the_cause(scope):
    request = SimpleNamespace(scope=scope)
    with pytest.raises(ImproperlyConfigured, match="No active aiodrf lifespan state"):
        get_lifespan_state(request, object)
