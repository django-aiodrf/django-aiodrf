"""WhiteNoise's dual-mode adapter preserves file semantics and cleanup."""

import asyncio
import gzip
import inspect
import threading
from unittest.mock import Mock

import pytest
from django.core.signals import request_finished
from django.http import HttpResponse
from django.test import RequestFactory, override_settings
from whitenoise.middleware import WhiteNoiseMiddleware

from aiodrf.contrib.whitenoise import whitenoise_middleware
from tests.ecosystem.test_servestatic import CONTENT, request


@pytest.fixture
def static_settings(tmp_path):
    root = tmp_path / "public"
    root.mkdir()
    (root / "site.css").write_bytes(CONTENT)
    (root / "site.css.gz").write_bytes(gzip.compress(CONTENT))
    (tmp_path / "private.txt").write_text("not public")
    with override_settings(
        DEBUG=False,
        ROOT_URLCONF="tests.ecosystem.test_servestatic",
        MIDDLEWARE=["aiodrf.contrib.whitenoise.whitenoise_middleware"],
        STATIC_URL="/static/",
        STATIC_ROOT=root,
        WHITENOISE_USE_FINDERS=False,
        WHITENOISE_AUTOREFRESH=False,
    ):
        yield


@pytest.mark.usefixtures("static_settings")
@pytest.mark.django_db(transaction=True)
class TestWhiteNoiseAdapter:
    async def test_asgi_get_head_and_conditional_requests(self):
        with pytest.warns(Warning, match="must consume synchronous iterators"):
            status, headers, body = await request()
        assert status == 200
        assert body == CONTENT
        with pytest.warns(Warning, match="must consume synchronous iterators"):
            assert (await request(method="HEAD"))[2] == b""
        with pytest.warns(Warning, match="must consume synchronous iterators"):
            conditional = await request(headers=[(b"if-none-match", headers[b"ETag"])])
        assert conditional[0] == 304
        assert conditional[2] == b""

    async def test_range_and_compression(self):
        with pytest.warns(Warning, match="must consume synchronous iterators"):
            status, _, body = await request(headers=[(b"range", b"bytes=2-7")])
        assert status == 206
        assert body == CONTENT[2:8]
        with pytest.warns(Warning, match="must consume synchronous iterators"):
            status, headers, body = await request(
                headers=[(b"accept-encoding", b"gzip")]
            )
        assert status == 200
        assert headers[b"Content-Encoding"] == b"gzip"
        assert gzip.decompress(body) == CONTENT

    @pytest.mark.parametrize("path", ["/static/missing", "/static/../private.txt"])
    async def test_missing_files_and_traversal(self, path):
        status, _, body = await request(path)
        assert status == 404
        assert b"not public" not in body

    async def test_delegates_to_django(self):
        assert (await request("/health/"))[0] == 200

    def test_sync_handler_and_static_response(self):
        downstream = Mock(return_value=HttpResponse("application"))
        handler = whitenoise_middleware(downstream)
        assert not inspect.iscoroutinefunction(handler)
        response = handler(RequestFactory().get("/static/site.css"))
        try:
            assert b"".join(response.streaming_content) == CONTENT
            downstream.assert_not_called()
        finally:
            response.close()
        req = RequestFactory().get("/api/")
        assert handler(req) is downstream.return_value
        downstream.assert_called_once_with(req)

    @pytest.mark.parametrize("autorefresh", [False, True])
    async def test_lookup_in_worker_and_downstream_on_loop(
        self, monkeypatch, autorefresh
    ):
        loop_thread = threading.get_ident()
        seen = []
        original = WhiteNoiseMiddleware.__call__

        def lookup(self, request):
            seen.append(threading.get_ident())
            return original(self, request)

        async def downstream(request):
            assert threading.get_ident() == loop_thread
            return HttpResponse()

        monkeypatch.setattr(WhiteNoiseMiddleware, "__call__", lookup)
        with override_settings(WHITENOISE_AUTOREFRESH=autorefresh):
            handler = whitenoise_middleware(downstream)
            assert inspect.iscoroutinefunction(handler)
            await handler(RequestFactory().get("/api/"))
        assert len(seen) == 1
        assert seen[0] != loop_thread

    @pytest.mark.parametrize("fail", [False, True])
    async def test_repeated_cancellation_drains_worker(self, monkeypatch, fail):
        entered = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()
        closed = []
        response = HttpResponse()
        response._resource_closers.append(lambda: closed.append(threading.get_ident()))

        def lookup(self, request):
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5), "test did not release lookup"
            if fail:
                raise OSError("read failed")
            return response

        async def downstream(request):
            pytest.fail("cancelled request reached downstream")

        monkeypatch.setattr(WhiteNoiseMiddleware, "__call__", lookup)
        handler = whitenoise_middleware(downstream)
        task = asyncio.create_task(handler(RequestFactory().get("/static/site.css")))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert response.closed is not fail
        if not fail:
            assert len(closed) == 1
            assert closed[0] != threading.get_ident()

    async def test_a_failed_close_keeps_the_cancellation(self, monkeypatch):
        entered = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()
        response = HttpResponse()

        def lookup(self, request):
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5), "test did not release lookup"
            return response

        def receiver(**kwargs):
            raise RuntimeError("receiver failed")

        async def downstream(request):
            pytest.fail("cancelled request reached downstream")

        monkeypatch.setattr(WhiteNoiseMiddleware, "__call__", lookup)
        handler = whitenoise_middleware(downstream)
        task = asyncio.create_task(handler(RequestFactory().get("/static/site.css")))
        request_finished.connect(receiver)
        try:
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
        finally:
            release.set()
            request_finished.disconnect(receiver)
        assert response.closed

    async def test_lookup_exception_propagates(self, monkeypatch):
        def lookup(self, request):
            raise OSError("read failed")

        async def downstream(request):
            return HttpResponse()

        monkeypatch.setattr(WhiteNoiseMiddleware, "__call__", lookup)
        with pytest.raises(OSError, match="read failed"):
            await whitenoise_middleware(downstream)(RequestFactory().get("/static/x"))
