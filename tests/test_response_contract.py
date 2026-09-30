"""Rendering contracts across factories, clients and a full ASGI connection."""

import asyncio
import codecs
import json
from contextlib import aclosing

import pytest
from asgiref.sync import async_to_sync, iscoroutinefunction
from django.core.asgi import get_asgi_application
from django.http import HttpResponse
from django.test import override_settings
from django.urls import path
from django.utils.functional import lazy
from rest_framework.exceptions import NotFound
from rest_framework.renderers import BrowsableAPIRenderer, JSONRenderer
from rest_framework.test import APIRequestFactory

from aiodrf.response import (
    EventStreamResponse,
    Response,
    ServerSentEvent,
    StreamingResponse,
)
from aiodrf.test import APIClient, AsyncAPIClient, AsyncAPIRequestFactory
from aiodrf.utils import run_sync
from aiodrf.views import APIView
from tests.asgi_driver import ASGIDriver, http_scope


class Rendered(APIView):
    authentication_classes = []
    permission_classes = []
    renderer_classes = [JSONRenderer, BrowsableAPIRenderer]

    async def get(self, request, code=200):
        if code == 404:
            raise NotFound("missing")
        return Response({"value": lazy(lambda: "resolved", str)()}, status=code)


urlpatterns = [path("<int:code>/", Rendered.as_view())]


@pytest.mark.parametrize("method", ["get", "head"])
@pytest.mark.parametrize("code", [200, 204, 304, 404])
@pytest.mark.parametrize("transport", ["sync", "async", "asgi"])
async def test_status_and_head_contracts(method, code, transport):
    with override_settings(ROOT_URLCONF=__name__):
        if transport == "sync":
            response = await run_sync(getattr(APIClient(), method))(f"/{code}/")
            status, body = response.status_code, response.content
        elif transport == "async":
            response = await getattr(AsyncAPIClient(), method)(f"/{code}/")
            status, body = response.status_code, response.content
        else:
            async with ASGIDriver(
                get_asgi_application(), http_scope(f"/{code}/", method=method.upper())
            ) as driver:
                await driver.incoming.put(
                    {"type": "http.request", "body": b"", "more_body": False}
                )
                await driver.finish()
                status = driver.sent[0]["status"]
                body = b"".join(item.get("body", b"") for item in driver.sent[1:])
        assert status == code
        # Django test clients strip these bodies; the ASGI handler follows
        # the application's response, leaving wire suppression to the server.
        if transport != "asgi" and (method == "head" or code in (204, 304)):
            assert body == b""
        else:
            assert json.loads(body) == (
                {"detail": "missing"} if code == 404 else {"value": "resolved"}
            )


@pytest.mark.parametrize("factory", [APIRequestFactory, AsyncAPIRequestFactory])
@pytest.mark.parametrize(
    "accept", ["application/json", "text/html", "application/unknown"]
)
async def test_factory_negotiation_and_renderer_context(factory, accept):
    response = await Rendered.as_view()(factory().get("/", HTTP_ACCEPT=accept))
    assert response.renderer_context["view"].__class__ is Rendered
    assert response.status_code == (406 if accept == "application/unknown" else 200)
    if iscoroutinefunction(response.render):
        await response.render()
    else:
        await run_sync(response.render)()
    assert response.is_rendered
    assert response.accepted_renderer.format == (
        "api" if accept == "text/html" else "json"
    )


@pytest.mark.parametrize("fail", [False, True])
def test_post_render_callback_replacement_and_failure_are_not_replayed(fail):
    calls = []
    response = Response({"value": 1})
    response.accepted_renderer = JSONRenderer()
    response.accepted_media_type = "application/json"
    response.renderer_context = {}
    replacement = HttpResponse(b"replacement")

    def callback(rendered):
        calls.append(rendered)
        if fail:
            raise ValueError("callback refused")
        return replacement

    response.add_post_render_callback(callback)
    if fail:
        with pytest.raises(ValueError, match="callback refused"):
            response.render()
    else:
        assert response.render() is replacement
    assert response.render() is response
    assert calls == [response]


def test_non_json_renderer_cannot_be_labelled_ndjson():
    with pytest.raises(ValueError, match="JSON renderer"):
        StreamingResponse([], renderer=BrowsableAPIRenderer())


@pytest.mark.parametrize("disconnect", [False, True])
async def test_streaming_middleware_closes_its_wrapper_and_owned_source(disconnect):
    closed = []

    def middleware(get_response):
        async def handle(request):
            response = await get_response(request)
            content = response.streaming_content

            async def wrapped():
                try:
                    async with aclosing(content):
                        async for chunk in content:
                            yield chunk
                finally:
                    closed.append("middleware")

            response.streaming_content = wrapped()
            return response

        return handle

    middleware.async_capable = True
    middleware.sync_capable = False

    class Stream(Rendered):
        async def get(self, request):
            async def items():
                try:
                    yield {"value": 1}
                    yield {"value": 2}
                finally:
                    closed.append("source")

            return StreamingResponse(items())

    # Importable settings path without a process-global test registry.
    from unittest.mock import patch

    with (
        override_settings(
            ROOT_URLCONF=(path("", Stream.as_view()),), MIDDLEWARE=["test.wrapper"]
        ),
        patch("django.core.handlers.base.import_string", return_value=middleware),
    ):
        async with ASGIDriver(
            get_asgi_application(), http_scope(), block_body=disconnect
        ) as driver:
            await driver.incoming.put(
                {"type": "http.request", "body": b"", "more_body": False}
            )
            if disconnect:
                await asyncio.wait_for(driver.blocked.wait(), 3)
                await driver.incoming.put({"type": "http.disconnect"})
            await driver.finish()
    assert sorted(closed) == ["middleware", "source"]


class EventDecoder:
    """Independent incremental UTF-8/LF EventSource decoder for the wire tests."""

    def __init__(self):
        self.decoder = codecs.getincrementaldecoder("utf-8")()
        self.buffer = ""
        self.data = []
        self.event = "message"
        self.id = ""
        self.retry = None
        self.events = []

    def feed(self, fragment):
        self.buffer += self.decoder.decode(fragment)
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            if not line:
                if self.data:
                    self.events.append(
                        ("\n".join(self.data), self.event, self.id, self.retry)
                    )
                self.data, self.event = [], "message"
                continue
            field, _, value = line.partition(":")
            value = value.removeprefix(" ")
            if field == "data":
                self.data.append(value)
            elif field == "event":
                self.event = value or "message"
            elif field == "id" and "\x00" not in value:
                self.id = value
            elif field == "retry" and value.isascii() and value.isdigit():
                self.retry = int(value)


@pytest.mark.parametrize("size", [1, 2, 7, 4096])
def test_fragmented_sse_preserves_utf8_metadata_and_id_reset(size):
    async def events():
        yield ServerSentEvent("ş🙂\r\nlast\r", event="更新", id=7, retry=0)
        yield ServerSentEvent("", id="")
        yield "next\u2028line"

    async def wire():
        return b": heartbeat\n\n" + b"".join(
            [chunk async for chunk in EventStreamResponse(events())]
        )

    raw = async_to_sync(wire)()
    decoder = EventDecoder()
    for start in range(0, len(raw), size):
        decoder.feed(raw[start : start + size])
    assert decoder.buffer == ""
    assert decoder.events == [
        ("ş🙂\nlast\n", "更新", "7", 0),
        ("", "message", "", 0),
        ("next\u2028line", "message", "", 0),
    ]
