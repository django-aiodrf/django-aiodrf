"""Resource ownership is checked with close counts and reachability, not RSS."""

import asyncio
import contextlib
import gc
import weakref

import pytest
from django.core.asgi import get_asgi_application
from django.http import StreamingHttpResponse
from django.test import override_settings
from django.urls import path
from rest_framework import serializers

from aiodrf import aio
from aiodrf.response import EventStreamResponse, StreamingResponse
from aiodrf.utils import run_sync
from tests.asgi_driver import ASGIDriver, http_scope

urlpatterns = []


@pytest.mark.parametrize(
    "response_class",
    [
        pytest.param(
            StreamingHttpResponse,
            marks=pytest.mark.xfail(
                strict=True,
                reason="Django's streaming_content wrapper does not close its source on send failure",
            ),
        ),
        StreamingResponse,
    ],
)
async def test_send_failure_closes_a_started_producer(response_class, monkeypatch):
    closed = []
    producers = []
    requests = []

    async def view(request):
        requests.append(request)

        async def producer():
            try:
                yield (
                    b"first"
                    if response_class is StreamingHttpResponse
                    else {"value": 1}
                )
                await asyncio.Event().wait()
            finally:
                closed.append(True)

        source = producer()
        producers.append(source)
        return response_class(source)

    monkeypatch.setattr(__name__ + ".urlpatterns", [path("stream/", view)])
    with override_settings(ROOT_URLCONF=__name__, MIDDLEWARE=[]):
        driver = ASGIDriver(get_asgi_application(), http_scope("/stream/"))

        async def send(message):
            if message["type"] == "http.response.body":
                raise OSError("connection closed")

        driver.send = send
        async with driver:
            await driver.incoming.put({"type": "http.request", "body": b""})
            # Django versions differ in whether transport errors escape.
            with contextlib.suppress(OSError):
                await driver.finish()
    try:
        assert closed == [True]
    finally:
        for source in producers:
            await source.aclose()
        # Older Django handlers do not reach request.close() on send failure.
        for request in requests:
            await run_sync(request.close)()


@pytest.mark.parametrize("response_class", [StreamingResponse, EventStreamResponse])
async def test_close_before_first_iteration_closes_owned_source_once(response_class):
    class Source:
        closed = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise AssertionError(
                "Unconsumed sources must not be advanced to close them"
            )

        async def aclose(self):
            self.closed += 1

    source = Source()
    response = response_class(source)
    await response.aclose()
    await response.aclose()
    assert source.closed == 1


async def test_dynamic_serializer_errors_do_not_pin_request_contexts():
    class Context:
        pass

    references = []

    async def validate(index):
        context = Context()
        references.append(weakref.ref(context))

        def validator(value):
            # A class-level field closure owns this request-like object.
            assert context is not None
            raise serializers.ValidationError("refused", code="contract")

        serializer_class = type(
            f"Transient{index}",
            (serializers.Serializer,),
            {"value": serializers.IntegerField(validators=[validator])},
        )
        references.append(weakref.ref(serializer_class))
        serializer = serializer_class(
            data={"value": index}, context={"request": context}
        )
        assert not await aio.is_valid(serializer)
        assert serializer.errors["value"][0].code == "contract"

    for index in range(64):
        await validate(index)
    await run_sync(lambda: None)()  # Release the worker's last completed invocation.
    gc.collect()  # Reachability assertion only; never add GC to the runtime request path.
    assert all(reference() is None for reference in references), [
        index for index, reference in enumerate(references) if reference() is not None
    ]


def _fail_headers(driver):
    async def send(message):
        if message["type"] == "http.response.start":
            raise OSError("connection closed before the headers")

    driver.send = send


async def _serve_with_failed_headers(view, monkeypatch):
    requests = []

    def recording(request):
        requests.append(request)
        return view(request)

    monkeypatch.setattr(__name__ + ".urlpatterns", [path("stream/", recording)])
    with override_settings(ROOT_URLCONF=__name__, MIDDLEWARE=[]):
        driver = ASGIDriver(get_asgi_application(), http_scope("/stream/"))
        _fail_headers(driver)
        async with driver:
            await driver.incoming.put({"type": "http.request", "body": b""})
            with contextlib.suppress(OSError):
                await driver.finish()
    # Django 5.2 and 6.0 do not close the request, and its body file, when
    # sending fails; the test does, not to report it in a later test.
    for request in requests:
        await run_sync(request.close)()


@pytest.mark.parametrize("response_class", [StreamingResponse, EventStreamResponse])
async def test_failed_headers_leave_nothing_open_when_the_producer_acquires_its_resources(
    response_class, monkeypatch
):
    # The documented pattern: resources are acquired inside the producer. If
    # the headers cannot be sent, the producer never starts, so it acquired
    # nothing and must not be advanced to find that out.
    acquired, released = [], []

    async def producer():
        acquired.append(True)
        try:
            yield {"value": 1}
        finally:
            released.append(True)

    await _serve_with_failed_headers(
        lambda request: response_class(producer()), monkeypatch
    )
    assert acquired == released == []


@pytest.mark.xfail(
    strict=True,
    reason="Django's ASGI handler does not close the response when sending the headers fails",
)
@pytest.mark.parametrize("response_class", [StreamingResponse, EventStreamResponse])
async def test_failed_headers_close_a_source_acquired_before_the_response(
    response_class, monkeypatch
):
    # An iterator opened by the view before it built the response (an HTTP
    # stream, say): Django neither iterates nor closes the response, so only
    # the source's own finalizer releases it. Acquire in the producer instead.
    class Source:
        closed = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise AssertionError("an unconsumed source must not be advanced")

        async def aclose(self):
            self.closed += 1

    sources = []

    def view(request):
        sources.append(Source())
        return response_class(sources[-1])

    await _serve_with_failed_headers(view, monkeypatch)
    assert sources[0].closed == 1
