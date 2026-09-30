"""
Cache backends under throttling and ``cache_page``.

A cache is where aiodrf has to know whether a call does I/O without being
told: DRF's throttles and Django's ``cache_page`` use whatever ``CACHES``
configures. Every test runs against every backend Django ships, and against
Redis (Django's backend and django-redis) when the server of
``tests/services/compose.yaml`` is up.
"""

import asyncio
import importlib.util
import os
import socket
import sys
import tempfile
import threading
import typing
import unittest
from unittest import mock

import pytest
from django.core.cache import caches
from django.core.cache.backends.base import DEFAULT_TIMEOUT
from django.core.cache.backends.locmem import LocMemCache
from django.core.exceptions import SynchronousOnlyOperation
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import path
from django.utils.asyncio import async_unsafe
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page as django_cache_page
from rest_framework import views as drf_views
from rest_framework.permissions import AllowAny
from rest_framework.response import Response as DRFResponse
from rest_framework.throttling import AnonRateThrottle

from aiodrf.cache import cache_page, is_in_process_cache
from aiodrf.response import Response
from aiodrf.test import APIClient, AsyncAPIClient, count_hops
from aiodrf.throttling import AnonFixedWindowRateThrottle, ScopedFixedWindowRateThrottle
from aiodrf.views import APIView

REDIS = ("127.0.0.1", int(os.environ.get("AIODRF_BENCH_REDIS_PORT", "6380")))
OPTIONS = {"KEY_PREFIX": "aiodrf", "VERSION": 2}
BACKENDS = {
    "locmem": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "matrix",
    },
    "dummy": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"},
    "database": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "aiodrf_test_cache",
    },
    "file": {"BACKEND": "django.core.cache.backends.filebased.FileBasedCache"},
    "redis": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": "redis://{}:{}/15".format(*REDIS),
    },
    "django_redis": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": "redis://{}:{}/14".format(*REDIS),
    },
}
IN_PROCESS = {"locmem", "dummy"}
NEEDS = {"redis": "redis", "django_redis": "django_redis"}


def redis_is_up():
    try:
        socket.create_connection(REDIS, timeout=0.2).close()
    except OSError:
        return False
    return True


def for_each_backend(cls):
    """Create ``<Name>_<backend>`` test cases from a mixin."""
    module = sys.modules[cls.__module__]
    for backend in BACKENDS:
        name = f"{cls.__name__.removeprefix('_')}_{backend}"
        attrs = {"__module__": cls.__module__, "backend": backend}
        setattr(module, name, type(name, (cls, TestCase), attrs))
    return cls


class BackendFixture:
    @classmethod
    def setUpClass(cls):
        package = NEEDS.get(cls.backend)
        if package is not None:
            if importlib.util.find_spec(package) is None:
                raise unittest.SkipTest(f"{package} is not installed")
            if not redis_is_up():
                raise unittest.SkipTest("no Redis server (tests/services/compose.yaml)")
        config = {**BACKENDS[cls.backend], **OPTIONS}
        if cls.backend == "file":
            config["LOCATION"] = cls.enterClassContext(tempfile.TemporaryDirectory())
        cls.enterClassContext(
            override_settings(CACHES={"default": config}, ROOT_URLCONF=__name__)
        )
        super().setUpClass()
        if cls.backend == "database":
            call_command("createcachetable", verbosity=0)

    def setUp(self):
        caches["default"].clear()
        self.addCleanup(caches["default"].clear)


# -- Throttling ---------------------------------------------------------------------


class DRFThrottle(AnonRateThrottle):
    scope = "drf"
    rate = "3/min"


class AioDRFThrottle(AnonRateThrottle):
    scope = "aiodrf"
    rate = "3/min"


class DRFThrottled(drf_views.APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [DRFThrottle]

    def get(self, request):
        return DRFResponse({"ok": True})


class Throttled(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [AioDRFThrottle]

    async def get(self, request):
        return Response({"ok": True})


@for_each_backend
class _ThrottleTests(BackendFixture):
    async def test_aiodrf_throttles_like_drf(self):
        client = AsyncAPIClient()
        for attempt in range(5):
            drf = await client.get("/drf/throttled/")
            aiodrf = await client.get("/aiodrf/throttled/")
            assert aiodrf.status_code == drf.status_code, attempt
            assert aiodrf.headers.get("Retry-After") == drf.headers.get("Retry-After")
            assert aiodrf.data == drf.data
        # A cache that stores nothing never throttles, in DRF as well.
        assert aiodrf.status_code == (200 if self.backend == "dummy" else 429)

    async def test_the_cache_is_used_on_the_event_loop_only_when_it_lives_in_the_process(
        self,
    ):
        with count_hops() as hops:
            response = await AsyncAPIClient().get("/aiodrf/throttled/")
        assert response.status_code == 200
        assert hops.count == (0 if self.backend in IN_PROCESS else 1), hops.calls

    def test_through_wsgi(self):
        statuses = [APIClient().get("/aiodrf/throttled/").status_code for _ in range(4)]
        assert statuses == [200, 200, 200, 200 if self.backend == "dummy" else 429]


class FixedWindow(AnonFixedWindowRateThrottle):
    scope = "fixed"
    rate = "3/min"


class FixedWindowThrottled(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [FixedWindow]

    async def get(self, request):
        return Response({"ok": True})


class ScopedFixedWindowThrottled(FixedWindowThrottled):
    throttle_classes = [ScopedFixedWindowRateThrottle]
    throttle_scope = "fixed_scoped"


# Stores nothing, or cannot increment atomically: the window is not enforced
# (Dummy) or only approximately under concurrency (database, file).
ATOMIC_INCREMENT = {"locmem", "redis", "django_redis"}


@for_each_backend
class _FixedWindowTests(BackendFixture):
    async def test_the_window_admits_the_rate_then_refuses_until_it_ends(self):
        client = AsyncAPIClient()
        with mock.patch.object(FixedWindow, "timer", return_value=120.0 + 50):
            statuses = [(await client.get("/fixed/")).status_code for _ in range(4)]
            refused = await client.get("/fixed/")
        if self.backend == "dummy":
            assert statuses == [200, 200, 200, 200]
            return
        assert statuses == [200, 200, 200, 429]
        # The window of 3/min that began at 120 s ends at 180 s.
        assert refused.headers["Retry-After"] == "10"
        with mock.patch.object(FixedWindow, "timer", return_value=180.0):
            assert (await client.get("/fixed/")).status_code == 200

    async def test_scoped_rates_come_from_the_view(self):
        client = AsyncAPIClient()
        rates = {"fixed_scoped": "2/min"}
        with mock.patch.object(ScopedFixedWindowRateThrottle, "THROTTLE_RATES", rates):
            statuses = [
                (await client.get("/fixed/scoped/")).status_code for _ in range(3)
            ]
        assert statuses == ([200] * 3 if self.backend == "dummy" else [200, 200, 429])

    async def test_the_cache_is_used_on_the_event_loop_only_when_it_lives_in_the_process(
        self,
    ):
        with count_hops() as hops:
            response = await AsyncAPIClient().get("/fixed/")
        assert response.status_code == 200
        # ``DummyCache`` inherits ``incr`` from ``BaseCache``, which is only
        # as pure as the backend's ``get`` and ``set``: not trusted.
        assert hops.count == (0 if self.backend == "locmem" else 1), hops.calls

    async def test_concurrent_requests_are_admitted_exactly_up_to_the_rate(self):
        if self.backend not in ATOMIC_INCREMENT:
            self.skipTest("no atomic increment")
        client = AsyncAPIClient()
        with mock.patch.object(FixedWindow, "rate", "5/min"):
            responses = await asyncio.gather(
                *(client.get("/fixed/") for _ in range(12))
            )
        assert sorted(r.status_code for r in responses) == [200] * 5 + [429] * 7

    def test_through_wsgi(self):
        statuses = [APIClient().get("/fixed/").status_code for _ in range(4)]
        assert statuses == [200, 200, 200, 200 if self.backend == "dummy" else 429]


class FixedWindowUnitTests(TestCase):
    def throttle(self, now):
        throttle = FixedWindow()
        throttle.timer = lambda: now
        return throttle

    def test_an_evicted_window_starts_again(self):
        # ``add`` found the key, ``incr`` did not: it was evicted in between.
        throttle = self.throttle(10.0)
        request = APIClient().get("/", REMOTE_ADDR="10.0.0.1").wsgi_request
        request = Throttled().initialize_request(request)
        throttle.cache = mock.Mock(add=mock.Mock(side_effect=[False, True]))
        throttle.cache.incr.side_effect = ValueError("evicted")
        assert throttle.allow_request(request, None)
        assert throttle.cache.add.call_count == 2

    def test_a_window_recreated_by_another_request_is_counted(self):
        # Evicted between ``add`` and ``incr``, then created again by another
        # request before this one's ``add``: this request is the second.
        throttle = self.throttle(10.0)
        throttle.num_requests = 1
        request = APIClient().get("/", REMOTE_ADDR="10.0.0.1").wsgi_request
        request = Throttled().initialize_request(request)
        throttle.cache = mock.Mock(add=mock.Mock(side_effect=[False, False]))
        throttle.cache.incr.side_effect = [ValueError("evicted"), 2]
        assert not throttle.allow_request(request, None)
        assert throttle.cache.incr.call_count == 2

    def test_no_cache_key_means_no_throttling(self):
        throttle = self.throttle(10.0)
        throttle.get_cache_key = lambda request, view: None
        throttle.cache = mock.Mock()
        assert throttle.allow_request(None, None)
        throttle.cache.add.assert_not_called()

    def test_the_key_names_the_window_and_outlives_it(self):
        throttle = self.throttle(179.2)
        throttle.get_cache_key = lambda request, view: "throttle_fixed_ip"
        throttle.cache = mock.Mock(add=mock.Mock(return_value=True))
        assert throttle.allow_request(None, None)
        # Window 2 of 60 s ends at 180 s; the entry lives a second longer so
        # it cannot expire while its window still counts on it.
        throttle.cache.add.assert_called_once_with("throttle_fixed_ip:2", 1, 2)


class TwoTierCache(LocMemCache):
    """In front of something remote: not in-process any more."""

    def get(self, key, default=None, version=None):
        return super().get(key, default, version)


class CountingCache(LocMemCache):
    async_safe = True
    reads = 0

    def get(self, key, default=None, version=None):
        type(self).reads += 1
        return super().get(key, default, version)


class InProcessDetectionTests(TestCase):
    def cache(self, backend, **options):
        settings = {"default": {"BACKEND": backend, **options}}
        self.enterContext(override_settings(CACHES=settings))
        return caches["default"]

    def test_subclasses_that_add_code_are_not_trusted(self):
        assert is_in_process_cache(
            self.cache("django.core.cache.backends.locmem.LocMemCache")
        )
        assert not is_in_process_cache(self.cache(f"{__name__}.TwoTierCache"))

    def test_unless_they_declare_themselves(self):
        assert is_in_process_cache(self.cache(f"{__name__}.CountingCache"))

    def test_the_proxy_and_the_backend_are_the_same_question(self):
        from django.core.cache import cache

        self.cache("django.core.cache.backends.db.DatabaseCache", LOCATION="unused")
        assert not is_in_process_cache(cache)
        assert not is_in_process_cache(caches["default"])

    async def test_a_throttle_with_a_cache_of_its_own(self):
        # DRF documents ``cache = caches["alternate"]`` on the throttle class.
        settings = {
            "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
            "alternate": {
                "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
                "LOCATION": self.enterContext(tempfile.TemporaryDirectory()),
            },
        }
        with override_settings(CACHES=settings, ROOT_URLCONF=__name__):

            class AlternateThrottle(AioDRFThrottle):
                cache = caches["alternate"]

            view = Throttled.as_view(throttle_classes=[AlternateThrottle])
            from aiodrf.test import AsyncAPIRequestFactory

            with count_hops() as hops:
                response = await view(AsyncAPIRequestFactory().get("/"))
        assert response.status_code == 200
        assert hops.count == 1, hops.calls


# -- cache_page -----------------------------------------------------------------------


class Counted(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    calls = 0

    @method_decorator(cache_page(60))
    async def get(self, request):
        type(self).calls += 1
        return Response({"calls": type(self).calls})


class CountedWithDjango(Counted):
    calls = 0

    @method_decorator(django_cache_page(60))
    async def get(self, request):
        type(self).calls += 1
        return Response({"calls": type(self).calls})


@for_each_backend
class _CachePageTests(BackendFixture):
    def setUp(self):
        super().setUp()
        Counted.calls = CountedWithDjango.calls = 0

    async def test_the_second_response_comes_from_the_cache(self):
        client = AsyncAPIClient()
        first = await client.get("/aiodrf/counted/")
        second = await client.get("/aiodrf/counted/")
        assert first.status_code == second.status_code == 200
        assert first.json() == {"calls": 1}
        # What comes back was pickled: an aiodrf ``Response``, rendered.
        assert second.json() == (
            {"calls": 2} if self.backend == "dummy" else {"calls": 1}
        )
        assert second.content == first.content or self.backend == "dummy"
        assert "max-age=60" in second.headers["Cache-Control"]

    async def test_head_and_vary(self):
        client = AsyncAPIClient()
        first = await client.get("/aiodrf/counted/")
        assert "Accept" in first.headers["Vary"]
        head = await client.head("/aiodrf/counted/")
        assert head.status_code == 200
        assert head.content == b""
        # Another representation is another cache entry.
        other = await client.get(
            "/aiodrf/counted/", HTTP_ACCEPT="application/json; indent=4"
        )
        expected = 1 if self.backend != "dummy" else 2
        assert Counted.calls == expected + 1
        assert other.json() == {"calls": Counted.calls}

    async def test_unsafe_methods_are_not_cached(self):
        client = AsyncAPIClient()
        await client.get("/aiodrf/counted/")
        response = await client.post("/aiodrf/counted/")
        assert response.status_code == 405

    def test_through_wsgi(self):
        client = APIClient()
        first = client.get("/aiodrf/counted/")
        second = client.get("/aiodrf/counted/")
        assert first.json() == {"calls": 1}
        assert second.json() == (
            {"calls": 2} if self.backend == "dummy" else {"calls": 1}
        )

    async def test_the_lookup_leaves_the_event_loop_unless_the_cache_is_in_process(
        self,
    ):
        client = AsyncAPIClient()
        await client.get("/aiodrf/counted/")
        with count_hops() as hops:
            await client.get("/aiodrf/counted/")
        assert hops.calls == (
            []
            if self.backend in IN_PROCESS
            else ["FetchFromCacheMiddleware.process_request"]
        )


class RecordingCache(LocMemCache):
    """Adds code, so it is not trusted as in-process; records the writer's thread."""

    writers: typing.ClassVar[list[int]] = []

    def set(self, key, value, timeout=DEFAULT_TIMEOUT, version=None):
        type(self).writers.append(threading.get_ident())
        return super().set(key, value, timeout, version)


class CachePageStoreTests(TestCase):
    @override_settings(
        ROOT_URLCONF=__name__,
        CACHES={
            "default": {"BACKEND": f"{__name__}.RecordingCache", "LOCATION": "store"}
        },
    )
    async def test_the_response_is_stored_off_the_event_loop(self):
        # ``cache_page`` stores the response from a post-render callback;
        # Django renders a response that has one in a thread.
        RecordingCache.writers.clear()
        Counted.calls = 0
        response = await AsyncAPIClient().get("/aiodrf/counted/")
        assert response.status_code == 200
        assert RecordingCache.writers
        assert threading.get_ident() not in RecordingCache.writers


class DjangoCachePageTests(TestCase):
    """Why ``aiodrf.cache.cache_page`` exists; if this fails, Django changed."""

    @override_settings(
        ROOT_URLCONF=__name__,
        CACHES={
            "default": {
                "BACKEND": "django.core.cache.backends.db.DatabaseCache",
                "LOCATION": "aiodrf_test_cache",
            }
        },
    )
    async def test_djangos_decorator_queries_on_the_event_loop(self):
        from asgiref.sync import sync_to_async

        await sync_to_async(call_command)("createcachetable", verbosity=0)
        with pytest.raises(SynchronousOnlyOperation):
            await AsyncAPIClient().get("/django/counted/")


urlpatterns = [
    path("drf/throttled/", DRFThrottled.as_view()),
    path("aiodrf/throttled/", Throttled.as_view()),
    path("aiodrf/counted/", Counted.as_view()),
    path("django/counted/", CountedWithDjango.as_view()),
    path("fixed/", FixedWindowThrottled.as_view()),
    path("fixed/scoped/", ScopedFixedWindowThrottled.as_view()),
]


class SetOffTheLoop(LocMemCache):
    # A local cache whose writes are the project's code: not pure.
    @async_unsafe("cache.set() ran on the event loop")
    def set(self, *args, **kwargs):
        return super().set(*args, **kwargs)


@override_settings(
    CACHES={
        "default": {"BACKEND": f"{__name__}.SetOffTheLoop", "LOCATION": "rendered"}
    },
    ALLOWED_HOSTS=["testserver"],
)
class RenderedResponseCacheTests(TestCase):
    async def test_an_already_rendered_response_is_stored_off_the_loop_once(self):
        from django.template.response import SimpleTemplateResponse
        from django.test import RequestFactory

        calls = []

        @cache_page(60)
        async def view(request):
            calls.append(request)
            response = SimpleTemplateResponse("unused")
            response.content = b"ready"
            return response

        first = await view(RequestFactory().get("/rendered/"))
        second = await view(RequestFactory().get("/rendered/"))
        assert (first.status_code, first.content) == (200, b"ready")
        assert (second.status_code, second.content) == (200, b"ready")
        assert len(calls) == 1  # the second one was a hit
