"""Native cache I/O, pool ownership and public API behavior through ASGI."""

import asyncio
import os
from contextlib import asynccontextmanager
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest
from aiodrf_asgi_lifespan.asgi import get_lifespan_state
from aiodrf_async_cache.django_valkey import LifespanConnectionFactory
from asgi_lifespan import LifespanManager
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
from django.urls import path
from django_valkey.async_cache.cache import AsyncValkeyCache
from django_valkey.async_cache.pool import AsyncConnectionFactory
from django_valkey.pool import ConnectionFactory
from valkey.asyncio.connection import BlockingConnectionPool, ConnectionPool
from valkey.exceptions import ConnectionError as ValkeyConnectionError

from aiodrf.asgi import get_asgi_application
from aiodrf.response import Response
from aiodrf.views import APIView

URL = os.environ.get("AIODRF_TEST_VALKEY_URL")


@pytest.mark.skipif(not URL, reason="Requires a dedicated Valkey test service")
async def test_contrib_backend_awaits_key_codec_and_factory_callbacks():
    from aiodrf_async_cache.valkey import AsyncValkeyCache as NativeCache

    class Codec:
        async def dumps(self, value):
            await asyncio.sleep(0)
            return str(value).encode()

        async def loads(self, value):
            await asyncio.sleep(0)
            return int(value)

    async def key(key, prefix, version):
        return f"{prefix}:{version}:{key}"

    async def default():
        return 7

    cache = NativeCache(
        URL,
        {
            "KEY_PREFIX": "valkey-callbacks-" + uuid4().hex,
            "KEY_FUNCTION": key,
            "OPTIONS": {"serializer": Codec},
        },
    )
    try:
        assert await cache.aget_or_set("value", default) == 7
        await cache.aset_many({"one": 1, "two": 2})
        assert await cache.aget_many(["one", "two"]) == {"one": 1, "two": 2}
        assert await cache.atouch("one", 10)
        assert not await cache.aadd("one", 9)
    finally:
        await cache.adelete_many(["value", "one", "two"])
        await cache.aclose()


def config(prefix):
    return {
        "LOCATION": URL or "valkey://127.0.0.1:6381/14",
        "KEY_PREFIX": prefix,
        "OPTIONS": {
            "CONNECTION_FACTORY": "aiodrf_async_cache.django_valkey.LifespanConnectionFactory",
            "CONNECTION_POOL_CLASS": "valkey.asyncio.connection.BlockingConnectionPool",
            "CONNECTION_POOL_KWARGS": {"max_connections": 8, "timeout": 2},
            "SOCKET_TIMEOUT": 2,
            "SOCKET_CONNECT_TIMEOUT": 2,
            "CLOSE_CONNECTION": True,
            "IGNORE_EXCEPTIONS": False,
        },
    }


def test_factory_does_not_mix_sync_async_or_loop_pools():
    options = config("pools")["OPTIONS"]
    sync = ConnectionFactory({})
    native = LifespanConnectionFactory(options)
    params = {"url": "valkey://pool-test.invalid:6379/0"}
    # The vendor's URL-only registry is shared by its sync and async factories.
    assert AsyncConnectionFactory._pools is ConnectionFactory._pools
    sync_pool = sync.get_connection_pool(params.copy())
    first = native.get_or_create_connection_pool(params.copy())
    second = native.get_or_create_connection_pool(params.copy())
    assert isinstance(first, ConnectionPool)
    assert first is second
    assert first is not sync_pool
    independent = LifespanConnectionFactory(options)
    assert independent.get_or_create_connection_pool(params.copy()) is not first


def test_factory_keeps_pool_limits_local_to_its_configuration():
    params = {"url": "valkey://pool-test.invalid:6379/0"}
    first = LifespanConnectionFactory(
        {"CONNECTION_POOL_KWARGS": {"max_connections": 1}}
    )
    second = LifespanConnectionFactory(
        {"CONNECTION_POOL_KWARGS": {"max_connections": 3}}
    )
    assert first.get_or_create_connection_pool(params.copy()).max_connections == 1
    assert second.get_or_create_connection_pool(params.copy()).max_connections == 3


def test_factory_rejects_a_synchronous_pool():
    with pytest.raises(ImproperlyConfigured, match="async"):
        LifespanConnectionFactory(
            {"CONNECTION_POOL_CLASS": "valkey.connection.ConnectionPool"}
        )


async def test_connection_error_is_not_converted_to_a_cache_miss():
    cache = AsyncValkeyCache(
        server=config("errors")["LOCATION"], params=config("errors")
    )
    with (
        patch.object(
            cache.client, "aget", side_effect=ValkeyConnectionError("offline")
        ),
        pytest.raises(ValkeyConnectionError, match="offline"),
    ):
        await cache.aget("missing")


@asynccontextmanager
async def lifespan():
    options = config("aiodrf-valkey-test-" + uuid4().hex)
    cache = AsyncValkeyCache(options["LOCATION"], options)
    # Initialize the client before concurrent requests can race lazy creation.
    await cache.client.get_client()
    try:
        yield cache
    finally:
        await cache.aclose()


class CachedValue(APIView):
    authentication_classes = []
    permission_classes = []
    throttle_classes = []

    async def get(self, request):
        cache = get_lifespan_state(request, AsyncValkeyCache)
        return Response({"value": await cache.aget("value")})


urlpatterns = [path("cache/", CachedValue.as_view())]


@pytest.mark.skipif(
    not URL, reason="Set AIODRF_TEST_VALKEY_URL for a dedicated Valkey service"
)
async def test_native_operations_through_asgi_and_shutdown():
    with override_settings(
        ROOT_URLCONF=__name__,
        AIODRF={**getattr(settings, "AIODRF", {})},
        DJANGO_LIFESPAN=lifespan,
    ):
        application = get_asgi_application()
        # ASGI lifespan state is copied into each request by LifespanManager.
        async with (
            LifespanManager(application) as manager,
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=manager.app),
                base_url="http://testserver",
            ) as client,
        ):
            response = await client.get("/cache/")
            assert response.json() == {"value": None}


@pytest.mark.skipif(
    not URL, reason="Set AIODRF_TEST_VALKEY_URL for a dedicated Valkey service"
)
async def test_native_batch_counter_expiry_and_loop_local_cleanup():
    async with lifespan() as cache:
        # Any synchronous cache bridge would fail this test.
        with patch(
            "asgiref.sync.SyncToAsync.__call__",
            side_effect=AssertionError("sync cache bridge"),
        ):
            await cache.aset("value", {"name": "Ada"}, timeout=30)
            assert await cache.aget("value") == {"name": "Ada"}
            await cache.aset_many({"one": 1, "two": 2}, timeout=30)
            assert await cache.aget_many(["one", "two"]) == {"one": 1, "two": 2}
            await cache.aset("counter", 0, timeout=30)
            async with asyncio.TaskGroup() as group:
                for _ in range(12):
                    group.create_task(cache.aincr("counter"))
            assert await cache.aget("counter") == 12
            await cache.aset("expired", "gone", timeout=0)
            assert await cache.aget("expired") is None
            await cache.adelete_many(["value", "one", "two", "counter"])
            assert await cache.aget("value") is None
            raw = await cache.client.get_client()
    # A closed pool cannot retain connected sockets after the lifespan exits.
    assert all(
        not connection.is_connected
        for connection in raw.connection_pool._available_connections
    )


@pytest.mark.skipif(
    not URL, reason="Set AIODRF_TEST_VALKEY_URL for a dedicated service"
)
async def test_async_pool_capacity_wait_is_cancellable_without_blocking_loop():
    options = config("pool-wait")
    options["OPTIONS"]["CONNECTION_POOL_KWARGS"] = {"max_connections": 1, "timeout": 1}
    cache = AsyncValkeyCache(options["LOCATION"], options)
    try:
        raw = await cache.client.get_client()
        assert isinstance(raw.connection_pool, BlockingConnectionPool)
        async with raw.pipeline() as holder:
            await holder.watch("aiodrf-pool-test")
            waiter = asyncio.create_task(raw.ping())
            await asyncio.sleep(0.01)
            assert not waiter.done()
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
        assert await raw.ping()
    finally:
        await cache.aclose()


@pytest.mark.skipif(
    not URL, reason="Set AIODRF_TEST_VALKEY_URL for a dedicated service"
)
async def test_repeated_requests_reuse_one_pool_and_startup_does_not_connect():
    options = config("reuse")
    cache = AsyncValkeyCache(options["LOCATION"], options)
    factory = cache.client.connection_factory
    try:
        with patch.object(
            factory, "get_connection_pool", wraps=factory.get_connection_pool
        ) as create:
            first = await cache.client.get_client()
            for _ in range(30):
                await cache.aget("missing")
                assert (
                    await cache.client.get_client()
                ).connection_pool is first.connection_pool
            assert create.call_count == 1
            assert (
                factory.get_or_create_connection_pool(
                    factory.make_connection_params(options["LOCATION"])
                )
                is first.connection_pool
            )
            assert create.call_count == 1
    finally:
        await cache.aclose()


@pytest.mark.skipif(
    not URL, reason="Set AIODRF_TEST_VALKEY_URL for a dedicated service"
)
@pytest.mark.parametrize(
    "backend",
    [
        "django_valkey.async_cache.cache.AsyncValkeyCache",
        "aiodrf_async_cache.valkey.AsyncValkeyCache",
    ],
)
async def test_page_middleware_and_lifespan_share_native_pool(backend):
    from tests.ecosystem.cache_contracts import check_page_cache

    params = config("valkey-pages-" + uuid4().hex)
    params["BACKEND"] = backend
    if backend.startswith("aiodrf_async_cache."):
        params["OPTIONS"] = {"socket_connect_timeout": 2, "socket_timeout": 2}
    await check_page_cache(params, "django_valkey.cache.ValkeyCache")
