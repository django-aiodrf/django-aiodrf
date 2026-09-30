"""
Streaming responses: newline-delimited JSON, a JSON array, server-sent
events. Each chunk leaves as it is produced; a disconnect stops the
producer; nothing of the application's runs on the event loop unless it is
known not to block.
"""

import asyncio
import contextvars
import json
import threading

import pytest
from django.core.asgi import get_asgi_application
from django.test import TestCase, override_settings
from django.urls import path
from django.utils.asyncio import async_unsafe
from django.utils.functional import lazy
from rest_framework.permissions import AllowAny

from aiodrf.response import (
    EventStreamResponse,
    ServerSentEvent,
    StreamingArrayResponse,
    StreamingResponse,
)
from aiodrf.test import AsyncAPIClient, count_hops
from aiodrf.views import APIView
from tests.testapp.models import Author
from tests.testapp.serializers import AuthorSerializer


class Open(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]


class Lines(Open):
    async def get(self, request):
        async def items():
            async for author in Author.objects.order_by("pk"):
                yield AuthorSerializer(author).data

        return StreamingResponse(items())


class Array(Open):
    async def get(self, request):
        async def items():
            async for author in Author.objects.order_by("pk"):
                yield AuthorSerializer(author).data

        return StreamingArrayResponse(items())


class FromAThread(Open):
    """A synchronous generator, reading as it goes: it runs in the thread."""

    async def get(self, request):
        def items():
            for author in Author.objects.order_by("pk").iterator():
                yield {"name": author.name, "thread": threading.get_ident()}

        return StreamingResponse(items(), chunk_size=2)


class Events(Open):
    finished = []

    async def get(self, request):
        async def events():
            try:
                yield ServerSentEvent({"n": 1}, event="count", id=1)
                yield "plain text\nsecond line"
                yield ServerSentEvent(b"bytes", retry=3000)
                await asyncio.sleep(3600)
            finally:
                type(self).finished.append(True)

        return EventStreamResponse(events())


class Slow(Open):
    async def get(self, request):
        async def events():
            await asyncio.sleep(0.05)
            yield "late"

        return EventStreamResponse(events(), keepalive=0.01)


urlpatterns = [
    path("lines/", Lines.as_view()),
    path("array/", Array.as_view()),
    path("thread/", FromAThread.as_view()),
    path("events/", Events.as_view()),
    path("slow/", Slow.as_view()),
]


async def collect(response):
    return b"".join([chunk async for chunk in response.streaming_content])


@override_settings(ROOT_URLCONF=__name__)
class StreamingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for name in ("Ursula", "Octavia", "Nnedi"):
            Author.objects.create(name=name)

    async def test_newline_delimited_json(self):
        with count_hops() as hops:
            response = await AsyncAPIClient().get("/lines/")
            body = await collect(response)
        assert response.status_code == 200
        assert response["Content-Type"] == "application/x-ndjson"
        lines = body.decode().splitlines()
        assert [json.loads(line)["name"] for line in lines] == [
            "Ursula",
            "Octavia",
            "Nnedi",
        ]
        assert body.endswith(b"\n")
        assert hops.calls == []  # Django's own hop reads the queryset

    async def test_json_array(self):
        response = await AsyncAPIClient().get("/array/")
        body = await collect(response)
        assert response["Content-Type"] == "application/json"
        assert [row["name"] for row in json.loads(body)] == [
            "Ursula",
            "Octavia",
            "Nnedi",
        ]

    async def test_a_synchronous_iterable_runs_in_the_thread_in_chunks(self):
        with count_hops() as hops:
            response = await AsyncAPIClient().get("/thread/")
            body = await collect(response)
        rows = [json.loads(line) for line in body.decode().splitlines()]
        assert [row["name"] for row in rows] == ["Ursula", "Octavia", "Nnedi"]
        assert {row["thread"] for row in rows} != {threading.get_ident()}
        # Three items, two per hop, one to find the end and one to close.
        assert hops.calls == ["StreamingResponse._rendered.<locals>.take"] * 3 + [
            "_StreamingResponse._close_sync_source"
        ]

    async def test_an_empty_array(self):
        await Author.objects.all().adelete()
        assert await collect(await AsyncAPIClient().get("/array/")) == b"[]"

    async def test_lazy_data_is_rendered_in_a_thread_once(self):
        evaluations = []

        def resolve():
            evaluations.append(threading.get_ident())
            return "resolved"

        async def items():
            yield {"value": lazy(resolve, str)()}
            yield {"value": 1}

        body = await collect(StreamingResponse(items()))
        assert body == b'{"value":"resolved"}\n{"value":1}\n'
        assert len(evaluations) == 1
        assert evaluations[0] != threading.get_ident()


@override_settings(ROOT_URLCONF=__name__)
class EventStreamTests(TestCase):
    def setUp(self):
        Events.finished.clear()

    async def test_the_format(self):
        async def events():
            yield ServerSentEvent({"n": 1}, event="count", id=1)
            yield "plain text\nsecond line"
            yield ServerSentEvent(b"bytes", retry=3000)
            yield ""

        response = EventStreamResponse(events())
        assert response["Content-Type"] == "text/event-stream"
        assert response["Cache-Control"] == "no-cache"
        assert await collect(response) == (
            b'event: count\nid: 1\ndata: {"n":1}\n\n'
            b"data: plain text\ndata: second line\n\n"
            b"retry: 3000\ndata: bytes\n\n"
            b"data: \n\n"
        )

    async def test_keepalive_comments_while_nothing_happens(self):
        response = await AsyncAPIClient().get("/slow/")
        chunks = [chunk async for chunk in response.streaming_content]
        assert chunks[-1] == b"data: late\n\n"
        assert chunks[:-1]
        assert set(chunks[:-1]) == {b":\n\n"}

    async def test_keepalive_keeps_the_producers_context_between_events(self):
        # Each event is awaited in a task of its own; a context variable the
        # producer sets must survive the wait, as it does without keepalive.
        step = contextvars.ContextVar("step", default="unset")

        async def events():
            step.set("set by the producer")
            yield "first"
            await asyncio.sleep(0.05)
            yield step.get()

        response = EventStreamResponse(events(), keepalive=0.01)
        chunks = [chunk async for chunk in response]
        assert chunks[0] == b"data: first\n\n"
        assert chunks[-1] == b"data: set by the producer\n\n"
        assert b":\n\n" in chunks[1:-1]

    def test_a_synchronous_iterable_is_refused(self):
        with pytest.raises(TypeError, match="async iterable"):
            EventStreamResponse(["not", "async"])

    async def test_a_disconnect_stops_the_producer(self):
        # Through Django's ASGI handler, which cancels the response on
        # ``http.disconnect``; the generator's ``finally`` runs.
        application = get_asgi_application()
        sent = []
        body_sent = False
        disconnect = asyncio.Event()

        async def receive():
            nonlocal body_sent
            if not body_sent:
                body_sent = True
                return {"type": "http.request", "body": b"", "more_body": False}
            await disconnect.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            sent.append(message)
            if sum(m["type"] == "http.response.body" for m in sent) == 3:
                disconnect.set()

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "path": "/events/",
            "raw_path": b"/events/",
            "query_string": b"",
            "headers": [(b"host", b"testserver")],
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 1),
            "scheme": "http",
        }
        await asyncio.wait_for(application(scope, receive, send), 5)
        bodies = [m["body"] for m in sent if m["type"] == "http.response.body"]
        assert bodies[0].startswith(b"event: count")
        assert Events.finished == [True]


async def test_sync_iterator_owns_its_worker_lifetime():
    calls = []

    class Items:
        @async_unsafe("iter on loop")
        def __iter__(self):
            calls.append(("iter", threading.get_ident()))
            return self

        @async_unsafe("next on loop")
        def __next__(self):
            calls.append(("next", threading.get_ident()))
            return {"value": 1}

        @async_unsafe("close on loop")
        def close(self):
            calls.append(("close", threading.get_ident()))

    response = StreamingResponse(Items(), chunk_size=1)
    iterator = aiter(response)
    assert await anext(iterator) == b'{"value":1}\n'
    await iterator.aclose()
    assert [name for name, _ in calls] == ["iter", "next", "close"]
    assert len({thread for _, thread in calls}) == 1
    assert calls[0][1] != threading.get_ident()


@pytest.mark.parametrize(
    "response_class", [StreamingResponse, StreamingArrayResponse, EventStreamResponse]
)
async def test_disconnect_during_send_closes_the_retained_producer(response_class):
    closed = []

    async def items():
        try:
            yield {"value": 1}
            await asyncio.Event().wait()
        finally:
            closed.append(True)

    source = items()
    response = response_class(source)
    from django.core.handlers.asgi import ASGIHandler

    async def send(message):
        if message["type"] == "http.response.body" and b"value" in message["body"]:
            raise asyncio.CancelledError

    try:
        with pytest.raises(asyncio.CancelledError):
            await ASGIHandler().send_response(response, send)
        assert closed == [True]
    finally:
        await source.aclose()


@pytest.mark.parametrize(
    "response_class", [StreamingResponse, StreamingArrayResponse, EventStreamResponse]
)
async def test_asgi_disconnect_while_send_is_blocked(response_class):
    closed = []
    blocked = asyncio.Event()

    async def items():
        try:
            yield {"value": 1}
            pytest.fail("The blocked send must prevent the next read")
        finally:
            closed.append(True)

    source = items()

    class Stream(Open):
        async def get(self, request):
            return response_class(source)

    incoming = asyncio.Queue()
    incoming.put_nowait({"type": "http.request", "body": b"", "more_body": False})

    async def receive():
        if not incoming.empty():
            return incoming.get_nowait()
        await blocked.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body" and b"value" in message["body"]:
            blocked.set()
            await asyncio.Event().wait()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "path": "/stream/",
        "query_string": b"",
        "headers": [(b"host", b"testserver")],
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 1),
        "scheme": "http",
    }
    try:
        with override_settings(ROOT_URLCONF=(path("stream/", Stream.as_view()),)):
            await asyncio.wait_for(get_asgi_application()(scope, receive, send), 5)
        assert blocked.is_set()
        assert closed == [True]
    finally:
        await source.aclose()


@pytest.mark.parametrize("name", ["id", "event"])
@pytest.mark.parametrize(
    "value", ["one\n\ndata: injected\n\n", "one\rtwo", "one\x00two"]
)
def test_sse_metadata_cannot_inject_records(name, value):
    with pytest.raises(ValueError, match=name):
        ServerSentEvent("safe", **{name: value})


@pytest.mark.parametrize("value", [-1, True, 1.5, "5"])
def test_sse_retry_must_be_a_nonnegative_integer(value):
    with pytest.raises(ValueError, match="retry"):
        ServerSentEvent("safe", retry=value)


@pytest.mark.parametrize(
    "value", ["", "\n", "first\n", "first\n\n", "a\u2028b", "a\u0085b", "a\u2029b"]
)
async def test_sse_text_round_trips(value):
    async def events():
        yield value

    wire = (await collect(EventStreamResponse(events()))).decode()
    # Decode LF-framed records independently of the production splitter.
    data = ""
    for line in wire.split("\n"):
        if line.startswith("data:"):
            field = line[5:]
            data += field.removeprefix(" ") + "\n"
        elif not line and data:
            assert data[:-1] == value
            return
    pytest.fail("No event was dispatched")


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "2"])
def test_invalid_chunk_sizes_fail_at_construction(value):
    with pytest.raises(ValueError, match="chunk_size"):
        StreamingResponse([], chunk_size=value)


@pytest.mark.parametrize("value", [0, -1, True, float("nan"), float("inf"), "2"])
def test_invalid_keepalive_fails_at_construction(value):
    async def events():
        yield "unused"

    with pytest.raises(ValueError, match="keepalive"):
        EventStreamResponse(events(), keepalive=value)


async def test_closing_a_heartbeat_cancels_and_joins_the_pending_read():
    closed = []
    tasks_before = asyncio.all_tasks()

    async def events():
        try:
            await asyncio.Event().wait()
            yield "unreachable"
        finally:
            closed.append(True)

    source = events()
    response = EventStreamResponse(source, keepalive=0.001)
    iterator = aiter(response)
    assert await asyncio.wait_for(anext(iterator), 2) == b":\n\n"
    await iterator.aclose()
    assert closed == [True]
    assert asyncio.all_tasks() == tasks_before


async def test_streaming_backpressure_limits_sync_work_ahead():
    advanced = []

    def items():
        for value in range(1000):
            advanced.append(value)
            yield value

    response = StreamingResponse(items(), chunk_size=2)
    iterator = aiter(response)
    assert await anext(iterator) == b"0\n"
    assert advanced == [0, 1]
    await iterator.aclose()
    assert advanced == [0, 1]


async def test_with_keepalive_the_producer_runs_in_one_task_from_start_to_close():
    # A resource bound to the task that first used it (django-async-backend's
    # connections) must see every step, and the close, in that task.
    tasks = []

    async def events():
        try:
            for value in ("a", "b", "c"):
                tasks.append(asyncio.current_task())
                await asyncio.sleep(0.02)
                yield value
        finally:
            tasks.append(asyncio.current_task())

    response = EventStreamResponse(events(), keepalive=0.005)
    chunks = [chunk async for chunk in response]
    assert [c for c in chunks if c != b":\n\n"] == [
        b"data: a\n\n",
        b"data: b\n\n",
        b"data: c\n\n",
    ]
    assert b":\n\n" in chunks
    assert len(tasks) == 4
    assert len(set(tasks)) == 1


async def test_with_keepalive_the_producer_does_not_run_ahead_of_the_client():
    advanced = []

    async def events():
        for value in range(5):
            advanced.append(value)
            yield value

    iterator = aiter(EventStreamResponse(events(), keepalive=1))
    assert await anext(iterator) == b"data: 0\n\n"
    await asyncio.sleep(0.02)
    assert advanced == [0]
    await iterator.aclose()


class CloseOnce:
    """An async iterator whose cleanup belongs to the task that iterated it."""

    def __init__(self, items=()):
        self.items = list(items)
        self.owner = None
        self.closers = []

    def __aiter__(self):
        return self

    async def __anext__(self):
        self.owner = asyncio.current_task()
        if not self.items:
            raise StopAsyncIteration
        return self.items.pop(0)

    async def aclose(self):
        if self.closers:
            raise RuntimeError("closed twice")
        self.closers.append(asyncio.current_task())


@pytest.mark.parametrize("items", [[], ["a"]])
async def test_with_keepalive_the_source_is_closed_once_by_its_owner(items):
    source = CloseOnce(items)
    response = EventStreamResponse(source, keepalive=0.01)
    chunks = [chunk async for chunk in response]
    assert [c for c in chunks if c != b":\n\n"] == [b"data: a\n\n" for _ in items]
    assert source.closers == [source.owner]
    await response.aclose()
    assert len(source.closers) == 1


async def test_an_unstarted_keepalive_stream_closes_its_source_once():
    source = CloseOnce(["a"])
    response = EventStreamResponse(source, keepalive=0.01)
    await response.aclose()
    assert len(source.closers) == 1


# -- None items -------------------------------------------------------------------
#
# DRF's JSONRenderer renders None as an empty body; an item of a stream is a
# JSON value.


async def _chunks(response):
    return b"".join([chunk async for chunk in response])


async def _values(*values):
    for value in values:
        yield value


@pytest.mark.parametrize("items", [_values(None, 1), [None, 1]], ids=["async", "sync"])
async def test_a_none_item_is_null_in_a_json_array(items):
    body = await _chunks(StreamingArrayResponse(items))
    assert json.loads(body) == [None, 1]


@pytest.mark.parametrize("items", [_values(None, 1), [None, 1]], ids=["async", "sync"])
async def test_a_none_item_is_a_null_line_of_ndjson(items):
    body = await _chunks(StreamingResponse(items))
    assert body == b"null\n1\n"


async def test_none_event_data_is_null():
    body = await _chunks(EventStreamResponse(_values(None)))
    assert body == b"data: null\n\n"


async def test_a_failed_close_does_not_hide_the_iterations_error():
    class Source:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise ValueError("iteration")

        async def aclose(self):
            raise RuntimeError("close")

    response = EventStreamResponse(Source(), keepalive=0.01)
    with pytest.raises(ValueError, match="iteration") as raised:
        await _chunks(response)
    assert "RuntimeError('close')" in "".join(raised.value.__notes__)
