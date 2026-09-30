"""
django-debug-toolbar's middleware is async-capable. Under ASGI it installs
its SQL instrumentation with ``sync_to_async``, in the request's sync thread,
which is the thread aiodrf's hops run in, so it sees their queries. Panels
that are not async-capable (the profiler) are off for ASGI requests.
"""

import re
from collections import Counter

import pytest
from asgiref.sync import AsyncToSync, SyncToAsync
from django.test import override_settings
from django.urls import include, path
from rest_framework import generics as drf_generics
from rest_framework.permissions import AllowAny
from rest_framework.renderers import BrowsableAPIRenderer, JSONRenderer

from aiodrf import generics
from aiodrf.response import StreamingResponse
from aiodrf.test import AsyncAPIClient, count_hops
from aiodrf.views import APIView
from tests.base import both_transports
from tests.testapp.models import Author
from tests.testapp.serializers import AuthorSerializer


class Policies:
    queryset = Author.objects.order_by("pk")
    serializer_class = AuthorSerializer
    authentication_classes = []
    permission_classes = [AllowAny]
    renderer_classes = [JSONRenderer, BrowsableAPIRenderer]


class DRFAuthors(Policies, drf_generics.ListAPIView):
    pass


class Authors(Policies, generics.ListAPIView):
    pass


class Stream(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    async def get(self, request):
        async def lines():
            async for author in Author.objects.order_by("pk"):
                yield {"name": author.name}

        return StreamingResponse(lines())


urlpatterns = [
    path("drf/", DRFAuthors.as_view()),
    path("aiodrf/", Authors.as_view()),
    path("stream/", Stream.as_view()),
    path("__debug__/", include("debug_toolbar.urls")),
]
toolbar = override_settings(
    ROOT_URLCONF=__name__,
    MIDDLEWARE=["debug_toolbar.middleware.DebugToolbarMiddleware"],
    DEBUG_TOOLBAR_CONFIG={
        "SHOW_TOOLBAR_CALLBACK": lambda request: True,
        # Django sets DEBUG to False for tests; the toolbar is wanted here.
        "IS_RUNNING_TESTS": False,
    },
)


def stored():
    from debug_toolbar.store import get_store

    return get_store()


def recorded_queries(request_id):
    return [query["sql"] for query in stored().panel(request_id, "SQLPanel")["queries"]]


def request_id(response):
    return re.search(r'data-request-id="([0-9a-f]+)"', response.content.decode()).group(
        1
    )


@both_transports
class _DebugToolbarTests:
    @classmethod
    def setUpTestData(cls):
        Author.objects.create(name="Ursula")

    def setUp(self):
        super().setUp()
        stored().clear()

    @toolbar
    async def test_the_toolbar_is_injected_into_the_browsable_api(self):
        drf = await self.api("get", "/drf/", HTTP_ACCEPT="text/html")
        aiodrf = await self.api("get", "/aiodrf/", HTTP_ACCEPT="text/html")
        for response in (drf, aiodrf):
            assert response.status_code == 200
            assert 'id="djDebug"' in response.content.decode()
        # The SQL panel saw the list's query, made in aiodrf's worker thread.
        assert recorded_queries(request_id(aiodrf)) == recorded_queries(request_id(drf))
        assert len(recorded_queries(request_id(aiodrf))) == 1

    @toolbar
    async def test_json_responses_are_recorded_for_the_history_panel(self):
        drf = await self.api("get", "/drf/")
        aiodrf = await self.api("get", "/aiodrf/")
        assert aiodrf.content == drf.content
        drf_id, aiodrf_id = list(stored().request_ids())
        assert recorded_queries(aiodrf_id) == recorded_queries(drf_id)
        assert stored().panel(aiodrf_id, "HistoryPanel")["request_url"] == "/aiodrf/"

    @toolbar
    async def test_the_profiler_panel_runs_only_under_wsgi(self):
        # Panels that are not async-capable are off for ASGI requests.
        self.client.cookies["djdtProfilingPanel"] = "on"
        assert (await self.api("get", "/aiodrf/")).status_code == 200
        (only,) = stored().request_ids()
        profiled = bool(dict(stored().panels(only)).get("ProfilingPanel"))
        assert profiled is (self.transport == "wsgi")

    @toolbar
    async def test_a_streaming_response_is_passed_through(self):
        response = await self.api("get", "/stream/")
        assert response.status_code == 200
        if response.is_async:
            content = b"".join([chunk async for chunk in response.streaming_content])
        else:
            content = b"".join(response.streaming_content)
        # One JSON document per line; the toolbar leaves the body alone.
        assert content == b'{"name":"Ursula"}\n'
        assert "djDebug" not in content.decode()


@toolbar
@pytest.mark.django_db(transaction=True)
async def test_the_toolbar_costs_no_aiodrf_hop(monkeypatch):
    calls = []
    for adapter in (SyncToAsync, AsyncToSync):
        original = adapter.__call__

        def traced(self, *args, _original=original, **kwargs):
            calls.append(type(self).__name__)
            return _original(self, *args, **kwargs)

        monkeypatch.setattr(adapter, "__call__", traced)
    counts = {}
    for name, middleware in (
        ("without", []),
        ("with", ["debug_toolbar.middleware.DebugToolbarMiddleware"]),
    ):
        with override_settings(MIDDLEWARE=middleware):
            client = AsyncAPIClient()
            calls.clear()
            with count_hops() as hops:
                response = await client.get("/aiodrf/")
            assert response.status_code == 200
            assert hops.calls == ["ListModelMixin._list"]
            counts[name] = Counter(calls)
    added = counts["with"]
    added.subtract(counts["without"])
    # The middleware stays on the loop; it calls the synchronous
    # ``SHOW_TOOLBAR_CALLBACK`` and installs the SQL instrumentation with
    # ``sync_to_async``. Neither returns to the loop through ``async_to_sync``.
    assert +added == Counter({"SyncToAsync": 2})
