"""
whitenoise serves static files from its middleware, in front of aiodrf views.
``WhiteNoiseMiddleware`` is synchronous only: under ASGI Django runs it in a
thread and returns to the loop for the view.
"""

from collections import Counter

import pytest
from asgiref.sync import AsyncToSync, SyncToAsync
from django.test import override_settings
from django.urls import path
from rest_framework.permissions import AllowAny

from aiodrf.asgi import get_asgi_application
from aiodrf.test import AsyncAPIClient, count_hops
from tests.asgi_driver import ASGIDriver, http_scope
from tests.base import both_transports
from tests.ecosystem.base import whoami_views

drf_whoami, whoami = whoami_views(
    authentication_classes=[], permission_classes=[AllowAny]
)
urlpatterns = [path("drf/", drf_whoami), path("aiodrf/", whoami)]
CSS = b"body { color: black; }\n"


@pytest.fixture
def static_root(tmp_path):
    (tmp_path / "site.css").write_bytes(CSS)
    return tmp_path


def whitenoise(static_root, middleware=("whitenoise.middleware.WhiteNoiseMiddleware",)):
    return override_settings(
        ROOT_URLCONF=__name__,
        MIDDLEWARE=list(middleware),
        STATIC_URL="/static/",
        STATIC_ROOT=static_root,
    )


@both_transports
class _WhiteNoiseTests:
    @pytest.fixture(autouse=True)
    def _static(self, static_root):
        self.static_root = static_root

    async def test_static_files_and_views_side_by_side(self):
        with whitenoise(self.static_root):
            static = await self.api("get", "/static/site.css")
            drf = await self.api("get", "/drf/")
            aiodrf = await self.api("get", "/aiodrf/")
            content = b"".join(
                [chunk async for chunk in static]
                if static.is_async
                else static.streaming_content
            )
        assert static.status_code == 200
        assert content == CSS
        assert static["content-type"].startswith("text/css")
        assert aiodrf.status_code == drf.status_code == 200
        assert aiodrf.json() == drf.json() == {"user": ""}


def crossings(monkeypatch):
    """Every sync/async crossing asgiref makes, aiodrf's hops included."""
    calls = []
    for adapter in (SyncToAsync, AsyncToSync):
        original = adapter.__call__

        def traced(self, *args, _original=original, **kwargs):
            calls.append(type(self).__name__)
            return _original(self, *args, **kwargs)

        monkeypatch.setattr(adapter, "__call__", traced)
    return calls


@pytest.mark.django_db(transaction=True)
async def test_the_synchronous_middleware_costs_two_crossings_and_no_aiodrf_hop(
    static_root, monkeypatch
):
    calls = crossings(monkeypatch)
    counts = {}
    for name, middleware in (
        ("without", ()),
        ("with", ("whitenoise.middleware.WhiteNoiseMiddleware",)),
    ):
        with whitenoise(static_root, middleware):
            client = AsyncAPIClient()
            calls.clear()
            with count_hops() as hops:
                response = await client.get("/aiodrf/")
            assert response.status_code == 200
            assert hops.calls == []
            counts[name] = list(calls)
    # Django's own crossings (request signals) are in both; whitenoise adds one
    # into its thread and one back to the loop for the view.
    added = Counter(counts["with"])
    added.subtract(counts["without"])
    assert +added == Counter({"SyncToAsync": 1, "AsyncToSync": 1})


@pytest.mark.django_db(transaction=True)
async def test_under_asgi_django_reads_the_whole_file_first(static_root):
    # whitenoise's file response has a synchronous iterator. Django's ASGI
    # handler cannot send it chunk by chunk, so it reads it all into memory
    # and warns. Not aiodrf's code path; serve large static files from the
    # proxy or a CDN under ASGI.
    with whitenoise(static_root):
        async with ASGIDriver(
            get_asgi_application(), http_scope("/static/site.css")
        ) as driver:
            await driver.incoming.put({"type": "http.request", "body": b""})
            with pytest.warns(Warning, match="must consume synchronous iterators"):
                start = await driver.receive()
            await driver.finish()
    assert start["status"] == 200
    body = b"".join(
        m.get("body", b"") for m in driver.sent if m["type"] == "http.response.body"
    )
    assert body == CSS
