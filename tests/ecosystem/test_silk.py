"""
django-silk records requests, responses and SQL queries. ``SilkyMiddleware``
is synchronous only, keeps the request being recorded in a thread-local, and
replaces ``SQLCompiler.execute_sql`` to see the queries. Under ASGI the
middleware and aiodrf's hops run in the request's one sync thread, so the
queries of aiodrf's worker are attributed to the request.
"""

from collections import Counter

import pytest
from asgiref.sync import AsyncToSync, SyncToAsync, sync_to_async
from django.db.models.sql.compiler import SQLCompiler
from django.test import override_settings
from django.urls import path
from rest_framework import generics as drf_generics
from rest_framework.permissions import AllowAny
from silk.models import Request as SilkRequest

from aiodrf import generics
from aiodrf.test import AsyncAPIClient, count_hops
from tests.base import both_transports
from tests.testapp.models import Author
from tests.testapp.serializers import AuthorSerializer


class Policies:
    queryset = Author.objects.order_by("pk")
    serializer_class = AuthorSerializer
    authentication_classes = []
    permission_classes = [AllowAny]


class DRFAuthors(Policies, drf_generics.ListCreateAPIView):
    pass


class Authors(Policies, generics.ListCreateAPIView):
    pass


urlpatterns = [path("drf/", DRFAuthors.as_view()), path("aiodrf/", Authors.as_view())]
silk = override_settings(
    ROOT_URLCONF=__name__,
    MIDDLEWARE=["silk.middleware.SilkyMiddleware"],
    SILKY_PYTHON_PROFILER=False,
)


@pytest.fixture(autouse=True)
def _restore_the_compiler():
    # Silk replaces ``SQLCompiler.execute_sql`` for the rest of the process on
    # its first request; the other ecosystem tests run without it.
    original = SQLCompiler.execute_sql
    yield
    SQLCompiler.execute_sql = original
    if hasattr(SQLCompiler, "_execute_sql"):
        del SQLCompiler._execute_sql


@sync_to_async
def recorded(path, method):
    """Method, status and the start of each query silk recorded for a request."""
    silk_request = SilkRequest.objects.filter(path=path, method=method).latest(
        "start_time"
    )
    queries = silk_request.queries.order_by("start_time")
    return silk_request.response.status_code, [
        query.query.split(" FROM ")[0] for query in queries
    ]


@both_transports
class _SilkTests:
    @classmethod
    def setUpTestData(cls):
        Author.objects.create(name="Ursula")

    @silk
    async def test_the_request_its_response_and_its_queries_are_recorded(self):
        for prefix in ("drf", "aiodrf"):
            assert (await self.api("get", f"/{prefix}/")).status_code == 200
            assert (
                await self.api("post", f"/{prefix}/", data={"name": "Bo"})
            ).status_code == 201
        for method in ("GET", "POST"):
            assert await recorded("/aiodrf/", method) == await recorded("/drf/", method)
        assert await recorded("/aiodrf/", "GET") == (
            200,
            ['SELECT "testapp_author"."id", "testapp_author"."name"'],
        )
        # Silk replaces ``SQLCompiler.execute_sql``; ``SQLInsertCompiler``
        # has its own, so an INSERT is not recorded, with DRF alike.
        assert await recorded("/aiodrf/", "POST") == (201, [])


@pytest.mark.django_db(transaction=True)
@silk
async def test_the_middleware_costs_two_crossings_and_no_aiodrf_hop(monkeypatch):
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
        ("with", ["silk.middleware.SilkyMiddleware"]),
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
    assert +added == Counter({"SyncToAsync": 1, "AsyncToSync": 1})
