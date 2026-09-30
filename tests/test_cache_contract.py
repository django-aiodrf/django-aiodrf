import asyncio

import pytest
from django.core.cache.backends.locmem import LocMemCache
from django.test import override_settings
from django.urls import path
from django.utils.asyncio import async_unsafe
from django.utils.decorators import method_decorator

from aiodrf.cache import cache_page
from aiodrf.response import Response, StreamingResponse
from aiodrf.test import AsyncAPIClient
from aiodrf.views import APIView


class FailingCache(LocMemCache):
    def __init__(self, name, params):
        super().__init__(name, params)
        self.phase = params["OPTIONS"]["phase"]

    @async_unsafe("cache read on loop")
    def get(self, *args, **kwargs):
        if self.phase == "get":
            raise OSError("cache unavailable")
        return super().get(*args, **kwargs)

    @async_unsafe("cache write on loop")
    def set(self, *args, **kwargs):
        raise OSError("cache unavailable")


@pytest.mark.parametrize("phase", ["get", "set"])
async def test_cache_failures_propagate_without_replaying_the_view(phase):
    calls = []

    class View(APIView):
        authentication_classes = []
        permission_classes = []

        @method_decorator(cache_page(60))
        async def get(self, request):
            calls.append(True)
            return Response({"ok": True})

    with (
        override_settings(
            ROOT_URLCONF=(path("", View.as_view()),),
            CACHES={
                "default": {
                    "BACKEND": f"{__name__}.FailingCache",
                    "LOCATION": "failure",
                    "OPTIONS": {"phase": phase},
                }
            },
        ),
        pytest.raises(OSError, match="cache unavailable"),
    ):
        await AsyncAPIClient().get("/")
    assert calls == ([] if phase == "get" else [True])


async def test_streaming_is_not_page_cached_and_tenant_responses_stay_separate():
    calls = []

    class View(APIView):
        authentication_classes = []
        permission_classes = []

        @method_decorator(cache_page(60))
        async def get(self, request, tenant):
            calls.append(tenant)

            async def items():
                yield {"tenant": tenant}

            return StreamingResponse(items())

    async def request(tenant):
        response = await AsyncAPIClient().get(f"/{tenant}/")
        return b"".join([chunk async for chunk in response])

    with override_settings(ROOT_URLCONF=(path("<int:tenant>/", View.as_view()),)):
        results = await asyncio.gather(*(request(tenant) for tenant in (1, 2, 1, 2)))
    assert results == [b'{"tenant":1}\n', b'{"tenant":2}\n'] * 2
    assert sorted(calls) == [1, 1, 2, 2]
