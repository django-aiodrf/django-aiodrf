"""Shared live-ASGI page-cache checks for optional native backends."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import httpx
from aiodrf_async_cache.lifespan import cache_lifespan
from asgi_lifespan import LifespanManager
from asgiref.sync import sync_to_async
from django.core.cache import caches
from django.middleware.cache import CacheMiddleware
from django.test import RequestFactory, override_settings
from django.urls import path
from django.utils.cache import patch_vary_headers

from aiodrf.asgi import get_asgi_application
from aiodrf.response import Response
from aiodrf.views import APIView


async def check_page_cache(config, synchronous_backend):
    """Store native responses, reuse them through standard Django middleware."""
    calls = []
    retained = []

    class View(APIView):
        authentication_classes = []
        permission_classes = []
        throttle_classes = []

        async def get(self, request):
            tenant = request.headers.get("X-Tenant", "public")
            calls.append(tenant)
            response = Response({"tenant": tenant})
            patch_vary_headers(response, ["X-Tenant"])
            return response

    @asynccontextmanager
    async def lifespan():
        async with cache_lifespan() as backend:
            retained.append(backend)
            with patch.object(
                backend, "aset", new_callable=AsyncMock, wraps=backend.aset
            ) as writes:
                try:
                    yield backend
                finally:
                    await backend.adelete_many(
                        [call.args[0] for call in writes.await_args_list]
                    )

    sync_config = {
        "BACKEND": synchronous_backend,
        "LOCATION": config["LOCATION"],
        "KEY_PREFIX": config["KEY_PREFIX"],
        "OPTIONS": {"CLOSE_CONNECTION": True},
    }
    with override_settings(
        ROOT_URLCONF=(path("cached/", View.as_view()),),
        MIDDLEWARE=[
            "aiodrf_async_cache.middleware.AsyncUpdateCacheMiddleware",
            "django.middleware.common.CommonMiddleware",
            "aiodrf_async_cache.middleware.AsyncFetchFromCacheMiddleware",
        ],
        CACHES={
            "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
            "native": config,
            "sync": sync_config,
        },
        AIODRF={},
        DJANGO_LIFESPAN=lifespan,
        FASTDRF={},
        CACHE_MIDDLEWARE_SECONDS=30,
        CACHE_MIDDLEWARE_ALIAS="sync",
    ):
        async with (
            LifespanManager(get_asgi_application()) as manager,
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=manager.app),
                base_url="http://testserver",
            ) as client,
        ):
            for tenant in ("one", "two", "one"):
                response = await client.get("/cached/", headers={"X-Tenant": tenant})
                assert response.status_code == 200
                assert response.json() == {"tenant": tenant}
            response = await client.head("/cached/", headers={"X-Tenant": "one"})
            assert response.status_code == 200
            assert response.content == b""
            assert calls == ["one", "two"]

            def read_synchronously():
                try:
                    request = RequestFactory().get(
                        "/cached/", HTTP_X_TENANT="two", HTTP_ACCEPT="*/*"
                    )
                    response = CacheMiddleware(lambda request: None).process_request(
                        request
                    )
                    assert response is not None
                    assert response.content == b'{"tenant":"two"}'
                finally:
                    caches["sync"].close()

            await sync_to_async(read_synchronously)()
    assert len(retained) == 1
