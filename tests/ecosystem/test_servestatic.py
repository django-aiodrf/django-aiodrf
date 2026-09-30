"""ServeStatic retains HTTP file semantics on Django's ASGI handler."""

import gzip

import pytest
from django.http import JsonResponse
from django.test import override_settings
from django.urls import path

from aiodrf.asgi import get_asgi_application
from tests.asgi_driver import ASGIDriver, http_scope


async def health(request):
    return JsonResponse({"ok": True})


urlpatterns = [path("health/", health)]
CONTENT = b"body { color: black; }\n" * 100


@pytest.fixture
def static_settings(tmp_path):
    root = tmp_path / "public"
    root.mkdir()
    (root / "site.css").write_bytes(CONTENT)
    (root / "site.css.gz").write_bytes(gzip.compress(CONTENT))
    (tmp_path / "private.txt").write_text("not public")
    with override_settings(
        DEBUG=False,
        ROOT_URLCONF=__name__,
        MIDDLEWARE=["servestatic.middleware.ServeStaticMiddleware"],
        STATIC_URL="/static/",
        STATIC_ROOT=root,
        SERVESTATIC_USE_FINDERS=False,
        SERVESTATIC_USE_MANIFEST=False,
        SERVESTATIC_USE_STATIC_ROOT=True,
        SERVESTATIC_AUTOREFRESH=False,
    ):
        yield


async def request(path="/static/site.css", *, method="GET", headers=()):
    scope = http_scope(path, method=method)
    scope["headers"].extend(headers)
    async with ASGIDriver(get_asgi_application(), scope) as driver:
        await driver.incoming.put({"type": "http.request", "body": b""})
        start = await driver.receive()
        await driver.finish()
    body = b"".join(
        message.get("body", b"")
        for message in driver.sent
        if message["type"] == "http.response.body"
    )
    return start["status"], dict(start["headers"]), body


@pytest.mark.usefixtures("static_settings")
class TestServeStatic:
    async def test_get_head_and_conditional_requests(self):
        status, headers, body = await request()
        assert status == 200
        assert body == CONTENT
        assert headers[b"Content-Type"].startswith(b"text/css")
        assert (await request(method="HEAD"))[2] == b""
        conditional = await request(headers=[(b"if-none-match", headers[b"ETag"])])
        assert conditional[0] == 304
        assert conditional[2] == b""

    async def test_range_and_compression(self):
        status, _, body = await request(headers=[(b"range", b"bytes=2-7")])
        assert status == 206
        assert body == CONTENT[2:8]
        status, headers, body = await request(headers=[(b"accept-encoding", b"gzip")])
        assert status == 200
        assert headers[b"Content-Encoding"] == b"gzip"
        assert gzip.decompress(body) == CONTENT

    @pytest.mark.parametrize("path", ["/static/missing", "/static/../private.txt"])
    async def test_unknown_files_and_parent_paths_are_not_exposed(self, path):
        status, _, body = await request(path)
        assert status == 404
        assert b"not public" not in body

    async def test_nonstatic_requests_reach_django(self):
        status, _, body = await request("/health/")
        assert status == 200
        assert body == b'{"ok": true}'
