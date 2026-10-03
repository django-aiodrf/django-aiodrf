"""Managed ASGI resources, configuration and request-state ownership."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from aiodrf_asgi_lifespan.asgi import (
    get_asgi_application,
    get_lifespan_state,
)
from aiodrf_asgi_lifespan.settings import get_lifespan_factory
from aiodrf_asgi_lifespan.signals import asgi_shutdown, asgi_startup
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
from django.urls import path

from aiodrf.checks import check_settings
from aiodrf.response import Response
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
    with override_settings(AIODRF={}, DJANGO_LIFESPAN=value, FASTDRF={}):
        assert check_settings(None) == []
        assert get_lifespan_factory() is configured_lifespan
        assert get_asgi_application().lifespan is configured_lifespan
        assert get_asgi_application(lifespan=None).lifespan is None
    assert get_lifespan_factory() is None


def test_checks_and_application_construction_do_not_call_factory():
    factory = Mock(side_effect=AssertionError("resource opened before startup"))
    with override_settings(AIODRF={}, DJANGO_LIFESPAN=factory, FASTDRF={}):
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
    with override_settings(AIODRF={}, DJANGO_LIFESPAN=value, FASTDRF={}):
        assert [message.id for message in check_settings(None)] == [code]
        with pytest.raises(error_type, match="LIFESPAN"):
            get_lifespan_factory()


def test_undecorated_async_factories_are_rejected_without_calling_them():
    async def coroutine():
        raise AssertionError("must not be called")

    async def generator():
        yield None

    class AsyncCallable:
        __call__ = staticmethod(coroutine)

    for factory in (coroutine, generator, AsyncCallable()):
        with override_settings(AIODRF={}, DJANGO_LIFESPAN=factory, FASTDRF={}):
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
    from aiodrf_asgi_lifespan.testing import lifespan

    from aiodrf.test import AsyncAPIClient

    async with lifespan(configured_lifespan) as state:
        client = AsyncAPIClient(lifespan=state)
        response = await client.get("/state/")
        assert response.status_code == 200, response.content
        assert (await client.get("/keys/")).data == {
            "keys": ["aiodrf_asgi_lifespan.state"]
        }
        assert (await client.get("/keys/")).data == {
            "keys": ["aiodrf_asgi_lifespan.state"]
        }
    # Closed with the block: a request made afterwards finds no live state.
    with pytest.raises(ImproperlyConfigured, match="No active lifespan"):
        await client.get("/state/")


@client_urls
async def test_the_request_factory_carries_the_lifespan_state():
    from aiodrf_asgi_lifespan.testing import lifespan

    from aiodrf.test import AsyncAPIRequestFactory

    async with lifespan(configured_lifespan) as state:
        request = AsyncAPIRequestFactory(lifespan=state).get("/state/")
        response = await StateView.as_view()(request)
        assert response.status_code == 200


@client_urls
async def test_the_lifespan_defaults_to_the_setting():
    from aiodrf_asgi_lifespan.testing import lifespan

    from aiodrf.test import AsyncAPIClient

    with override_settings(AIODRF={}, DJANGO_LIFESPAN=configured_lifespan, FASTDRF={}):
        async with lifespan() as state:
            response = await AsyncAPIClient(lifespan=state).get("/state/")
            assert response.status_code == 200


async def test_the_test_lifespan_sends_the_signals_around_the_resource():
    from aiodrf_asgi_lifespan.testing import lifespan

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
    from aiodrf_asgi_lifespan.testing import lifespan

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
    from aiodrf_asgi_lifespan.testing import lifespan

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
    with pytest.raises(ImproperlyConfigured, match="No active lifespan state"):
        get_lifespan_state(request, object)
