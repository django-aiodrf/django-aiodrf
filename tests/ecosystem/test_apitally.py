"""
apitally: its synchronous Django middleware in front of aiodrf views.

The client's network sync loop and its instance lock files are stubbed; the
request metrics it collects are read from the client directly.
"""

import threading
import uuid
import warnings
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import path
from rest_framework.permissions import AllowAny

from aiodrf.response import Response
from aiodrf.test import AsyncAPIClient, count_hops
from aiodrf.views import APIView
from tests.base import both_transports
from tests.ecosystem.base import whoami_views
from tests.settings import MIDDLEWARE

# apitally's dependency backoff calls ``asyncio.iscoroutinefunction`` when it is
# imported, deprecated in CPython 3.14; warnings are errors in this suite.
with warnings.catch_warnings():
    warnings.filterwarnings(
        "ignore", "'asyncio.iscoroutinefunction' is deprecated", DeprecationWarning
    )
    from apitally.client.client_threading import ApitallyClient
    from apitally.django import ApitallyMiddleware

THREADS = {}


class RecordingApitallyMiddleware(ApitallyMiddleware):
    def __call__(self, request):
        THREADS["middleware"] = threading.current_thread()
        return super().__call__(request)


class Handler(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    async def get(self, request):
        THREADS["handler"] = threading.current_thread()
        return Response({"ok": True})


drf_whoami, whoami = whoami_views(
    authentication_classes=[], permission_classes=[AllowAny]
)
urlpatterns = [
    path("drf/", drf_whoami, name="drf-whoami"),
    path("aiodrf/", whoami, name="aiodrf-whoami"),
    path("handler/", Handler.as_view(), name="handler"),
]
apitally = override_settings(
    ROOT_URLCONF=__name__,
    MIDDLEWARE=[f"{__name__}.RecordingApitallyMiddleware", *MIDDLEWARE],
    APITALLY_MIDDLEWARE={
        "client_id": str(uuid.uuid4()),
        "env": "test",
        "urlconf": __name__,
    },
)


class StubbedClient:
    def setUp(self):
        super().setUp()
        for patcher in (
            mock.patch.object(ApitallyClient, "start_sync_loop"),
            mock.patch(
                "apitally.client.client_base.get_or_create_instance_uuid",
                return_value=(str(uuid.uuid4()), None),
            ),
            # A new client and configuration for every test.
            mock.patch.object(ApitallyClient, "_instance", None),
            mock.patch.object(ApitallyMiddleware, "config", None),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def counted(self):
        counts = ApitallyClient.get_instance().request_counter.request_counts
        return {
            (info.method, info.path, info.status_code): n for info, n in counts.items()
        }


@both_transports
class _ApitallyTests(StubbedClient):
    @apitally
    async def test_aiodrf_views_are_counted_like_drf_views(self):
        for url in ("/drf/", "/aiodrf/"):
            response = await self.api("get", url)
            assert response.status_code == 200
        assert self.counted() == {("GET", "/drf/", 200): 1, ("GET", "/aiodrf/", 200): 1}

    @apitally
    async def test_drf_endpoint_introspection_finds_aiodrf_views(self):
        await self.api("get", "/aiodrf/")
        paths = ApitallyClient.get_instance()._startup_data["paths"]
        assert {"method": "GET", "path": "/aiodrf/"} in paths
        assert {"method": "GET", "path": "/drf/"} in paths


@apitally
class AsgiTests(StubbedClient, TestCase):
    async def test_the_middleware_runs_in_a_thread_and_the_handler_on_the_loop(self):
        # apitally's middleware is synchronous only: under ASGI Django runs it
        # in a worker thread and the rest of the chain back on the event loop.
        # These are Django's adapters, not aiodrf's hops.
        with count_hops() as hops:
            response = await AsyncAPIClient().get("/handler/")
        assert response.status_code == 200
        assert THREADS["handler"] is threading.current_thread()
        assert THREADS["middleware"] is not threading.current_thread()
        assert hops.calls == []
        assert self.counted() == {("GET", "/handler/", 200): 1}
