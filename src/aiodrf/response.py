"""
Responses of async views.

:class:`Response` is DRF's, rendered on the event loop when that is known to
be safe. django-fastdrf's :class:`fastdrf.response.DataResponse` (opt-in), which
aiodrf's views resolve, is Django's ``HttpResponse`` with the content DRF's
JSON renderers produce, without DRF's template response. The streaming responses render one item at a time from an async
iterable: :class:`StreamingResponse` as newline-delimited JSON,
:class:`StreamingArrayResponse` as one JSON array, and
:class:`EventStreamResponse` as server-sent events. Django's ASGI handler
sends each chunk as it is produced and cancels the iteration when the client
disconnects; under WSGI Django consumes an async iterator whole before it
answers, which it says with a warning.
"""

import asyncio
import contextlib
import contextvars
import datetime
import decimal
import marshal
import math
import uuid
from collections import OrderedDict
from collections.abc import (
    AsyncGenerator,
    AsyncIterable,
    AsyncIterator,
    Generator,
    Iterable,
    Mapping,
)
from typing import TYPE_CHECKING, Any, cast
from zoneinfo import ZoneInfo

from asgiref.sync import markcoroutinefunction
from django.http import HttpResponseBase, StreamingHttpResponse
from django.template.response import SimpleTemplateResponse
from fastdrf import response as _fastdrf_response

# django-fastdrf's, which maintains them: the renderers DataResponse and the
# event loop render with, and the responses that release their request
# objects.
from fastdrf.renderers import _DATA_RENDERERS
from fastdrf.response import DataResponse, _render_data
from rest_framework import renderers, response
from rest_framework.exceptions import ErrorDetail
from rest_framework.utils.serializer_helpers import ReturnDict, ReturnList

import aiodrf._builtins  # noqa: F401 -- registers DRF's pure renderers
from aiodrf.hooks import require_sync_hooks
from aiodrf.utils import is_framework_class, is_pure, run_sync, user_defines

__all__ = [
    "EventStreamResponse",
    "Response",
    "ServerSentEvent",
    "StreamingArrayResponse",
    "StreamingResponse",
]


# What DRF's encoder converts without running anything of the application's.
_PLAIN = frozenset(
    (
        type(None),
        bool,
        int,
        float,
        str,
        ErrorDetail,
        datetime.date,
        datetime.timedelta,
        decimal.Decimal,
        uuid.UUID,
        bytes,
    )
)
_PLAIN_KEYS = frozenset((str, int, float, bool, type(None), ErrorDetail))

# Renderers whose encoder would evaluate what ``_plain_data`` refuses (lazy
# strings, QuerySets): they render on the loop only after that check. Exact
# classes, so a subclass that changes the encoder is not assumed to be safe.
_PLAIN_TIMEZONES = frozenset((type(None), datetime.timezone, ZoneInfo))


# Exact types only. ``OrderedDict`` is still returned by paginators written
# before dicts kept their order (drf-tweaks); the encoder follows its order.
_PLAIN_MAPPINGS = frozenset({dict, ReturnDict, OrderedDict})
_PLAIN_CONTAINERS = _PLAIN_MAPPINGS | {list, tuple, ReturnList}
_BUILTIN_CONTAINERS = frozenset({dict, list, tuple})


def _builtins_only(value: Any) -> bool:
    """
    True if ``value`` holds exact built-in types only: ``marshal`` refuses
    any other type, subclasses included, without calling it, at C speed.
    What it accepts besides JSON's types (sets, bytes) DRF's encoder
    converts or refuses without the application's code.
    """
    try:
        marshal.dumps(value)
    except ValueError:
        return False
    return True


# DRF's ``serializer.data`` containers, which change nothing of ``list`` and
# ``dict`` but refer to their serializer: their built-in counterpart.
_RETURNED_AS = {ReturnList: list, ReturnDict: dict}


def _builtin_container(value: Any, kind: Any) -> Any:
    """
    ``value`` as ``marshal`` can read it: DRF's containers copied, shallowly,
    when the instance holds nothing but DRF's ``serializer`` (the encoder
    calls a dict subclass's ``items``, which an instance may replace).
    """
    builtin = _RETURNED_AS.get(kind)
    if builtin is None or not vars(value).keys() <= _RETURNED_STATE:
        return value
    return builtin(value)


_RETURNED_STATE = frozenset({"serializer"})


def _plain_data(data: Any) -> bool:
    """Inspect exact known types without invoking payload callbacks."""
    # A cached page or a native driver's document: built-in types only.
    if _builtins_only(_builtin_container(data, type(data))):
        return True
    # Keep containers, not copies of their elements. A flat list needs no
    # additional list proportional to its length and no Python call per leaf.
    pending: list[Iterable[Any]] = [(data,)]
    seen = set()
    while pending:
        for value in pending.pop():
            kind = type(value)
            # Hashing or comparing a class can itself call a custom metaclass.
            # Reject it before membership tests, without evaluating the payload.
            if type(kind) is not type:
                return False
            if kind in _PLAIN:
                continue
            if kind in (datetime.datetime, datetime.time):
                zone = type(value.tzinfo)
                if type(zone) is not type or zone not in _PLAIN_TIMEZONES:
                    return False
                continue
            if kind not in _PLAIN_CONTAINERS:
                return False
            if kind in _BUILTIN_CONTAINERS and _builtins_only(value):
                continue
            if kind in _RETURNED_AS and _builtins_only(_builtin_container(value, kind)):
                continue
            identity = id(value)
            if identity in seen:
                continue  # The actual encoder reports circular references.
            seen.add(identity)
            if kind in _PLAIN_MAPPINGS:
                # The encoder calls ``items()``; one set on the instance is a callback.
                if kind is not dict and "items" in vars(value):
                    return False
                for key in dict.keys(value):
                    key_type = type(key)
                    if key_type is not str and (
                        type(key_type) is not type or key_type not in _PLAIN_KEYS
                    ):
                        return False
                pending.append(dict.values(value))
            else:
                pending.append(value)
    return True


def _framework_data_renderer(renderer_class: type) -> bool:
    """
    A renderer of DRF's or django-fastdrf's that renders a payload with no
    code but its own: plain data renders on the loop. A class the project
    registered with ``fastdrf.registry.register_data_renderer`` is the
    project's code, which renders on the loop only when declared pure.
    """
    return renderer_class in _DATA_RENDERERS and is_framework_class(renderer_class)


@markcoroutinefunction
def _render_checked(instance: Any) -> Any:
    """Render known payloads inline; unknown values keep their worker boundary."""
    if _plain_data(instance.data):
        renderer = instance.accepted_renderer
        kept = _DATA_RENDERERS.get(type(renderer))
        if kept is None:
            return SimpleTemplateResponse.render(instance)
        # For this call only: the same bytes, without building an encoder.
        instance.accepted_renderer = kept
        try:
            return SimpleTemplateResponse.render(instance)
        finally:
            instance.accepted_renderer = renderer
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return SimpleTemplateResponse.render(instance)
    return run_sync(SimpleTemplateResponse.render)(instance)


@markcoroutinefunction
def _render_inline(instance: Any) -> Any:
    """Render with an explicitly declared pure renderer."""
    return SimpleTemplateResponse.render(instance)


class _Render:
    """
    ``render`` descriptor that lets Django render JSON responses on the loop.

    Django's async handler awaits ``response.render()`` when ``render`` is a
    coroutine function and otherwise runs it in a thread; its sync handler,
    the test client and the cache middleware call it synchronously. For
    renderers that are pure CPU work this descriptor returns a function that
    is *marked* as a coroutine function but renders synchronously and returns
    the response, which is itself awaitable. Both kinds of caller therefore
    get a rendered response and the async handler saves a thread hop.

    DRF's ``JSONRenderer`` and django-fastdrf's registered JSON renderers run
    on the loop only after a structural check of the payload. Unknown values
    go to the worker before encoding
    starts. A renderer the project declared pure renders on the loop as
    declared. Every other renderer (the browsable API, templates) keeps the
    ordinary method, so Django renders it in a thread.
    """

    def __get__(self, instance: Any, owner: Any = None) -> Any:
        if instance is None:
            return SimpleTemplateResponse.render
        renderer = getattr(instance, "accepted_renderer", None)
        if (
            _framework_data_renderer(type(renderer))
            and not vars(renderer)
            and not instance._is_rendered
            and not instance._post_render_callbacks
        ):
            # A framework renderer as its class defines it (Django looks
            # ``render`` up four times per response). An already marked
            # function is bound: no closure is allocated per lookup.
            return _render_checked.__get__(instance, owner)
        if renderer is not None:
            require_sync_hooks(renderer, "render")
        if (
            instance._is_rendered
            or renderer is None
            # Post-render callbacks (``cache_page`` stores the response from
            # one) may block; Django runs them in a thread with ``render``.
            or instance._post_render_callbacks
        ):
            return SimpleTemplateResponse.render.__get__(instance, owner)

        if _framework_data_renderer(type(renderer)):
            # An instance can replace render(), get_indent() or its encoder.
            # Payload inspection does not establish that such code is pure.
            return SimpleTemplateResponse.render.__get__(instance, owner)

        if is_pure(renderer, "render"):
            return _render_inline.__get__(instance, owner)
        return SimpleTemplateResponse.render.__get__(instance, owner)


class Response(_fastdrf_response.Response):
    """
    DRF's ``Response`` with an inline render path for JSON-like renderers,
    which releases its request objects when closed (django-fastdrf's).

    Instances are awaitable (awaiting returns the response itself) so that
    Django's async handler can await the result of ``render()``.
    """

    render = _Render()

    def __await__(self) -> Generator[Any, None, Any]:
        return self
        yield  # pragma: no cover - makes this a generator


def resolve_data_response(
    response: "DataResponse", view: Any, request: Any
) -> HttpResponseBase:
    """
    The response a view answers with for a :class:`DataResponse`: rendered by
    one of DRF's JSON renderers, else DRF's :class:`Response` for it.
    """
    renderer = _data_renderer(view, request)
    if renderer is None:
        return _drf_response(response)
    return _render_data(response, view, request, renderer)


async def aresolve_data_response(
    response: "DataResponse", view: Any, request: Any, *, inline: bool = True
) -> HttpResponseBase:
    """:func:`resolve_data_response` on the event loop when that is safe."""
    renderer = _data_renderer(view, request)
    if renderer is None:
        return _drf_response(response)
    if inline and _plain_data(response.data):
        return _render_data(response, view, request, renderer)
    # The encoder would evaluate what it cannot encode as it is.
    return await run_sync(_render_data)(response, view, request, renderer)


def _data_renderer(view: Any, request: Any) -> Any:
    # django-fastdrf's, and aiodrf's async ``finalize_response`` too: the
    # project's may change the data after DRF's, which renders later.
    if user_defines(view, "afinalize_response"):
        return None
    return _fastdrf_response._data_renderer(view, request)


def _drf_response(data_response: Any) -> Response:
    return _fastdrf_response._drf_response(data_response, Response)


def upgrade_response(resp: HttpResponseBase) -> HttpResponseBase:
    """Give plain DRF responses returned by user code aiodrf's render path."""
    if type(resp) is response.Response:
        resp.__class__ = Response
    return resp


# -- Streaming ------------------------------------------------------------------


async def _arender(renderer: Any, data: Any) -> Any:
    """
    Render ``data`` with ``renderer``: on the event loop for DRF's
    ``JSONRenderer`` and django-fastdrf's registered JSON renderers (in a thread
    when the data holds something they would have to evaluate) and for renderers declared pure, in a thread for
    every other.
    """
    if _framework_data_renderer(type(renderer)):
        if not vars(renderer) and _plain_data(data):
            return _render_item(_DATA_RENDERERS[type(renderer)] or renderer, data)
    elif is_pure(renderer, "render"):
        return _render_item(renderer, data)
    return await run_sync(_render_item)(renderer, data)


def _render_item(renderer: Any, item: Any) -> Any:
    rendered = renderer.render(item)
    # DRF's ``JSONRenderer`` renders None as an empty response body; an item
    # of a stream is a JSON value.
    return b"null" if item is None and not rendered else rendered


class _StreamingResponse(StreamingHttpResponse):
    """Own the producer until consumption or an explicit async close ends."""

    if TYPE_CHECKING:
        # Django's private iterator of the streaming content, and the
        # producer each subclass defines.
        _iterator: Any

        def _stream(self, source: Any) -> AsyncGenerator[bytes, None]: ...

    def __init__(self, source: Any, **kwargs: Any) -> None:
        self._source = source
        self._source_iterator: Any = None
        self._source_closed = False
        self._content_closed = False
        self._body_iterator = self._stream(source)
        super().__init__(self._body_iterator, **kwargs)

    async def __aiter__(self) -> AsyncIterator[bytes]:
        content = (
            cast(AsyncIterator[bytes], self.streaming_content)
            if self.is_async
            else super().__aiter__()
        )
        try:
            async for chunk in content:
                yield chunk
        finally:
            try:
                if close := getattr(content, "aclose", None):
                    await close()
            finally:
                await self.aclose()

    async def aclose(self) -> None:
        """Close a partially consumed response, including its producer."""
        try:
            # Django's streaming_content getter wraps _iterator but does not
            # propagate aclose() to it. Middleware may have replaced it with
            # a resource-owning async iterator, which must finish as well.
            if not self._content_closed:
                self._content_closed = True
                if (
                    self.is_async
                    and self._iterator is not self._body_iterator
                    and (close := getattr(self._iterator, "aclose", None))
                ):
                    await close()
        finally:
            try:
                await self._body_iterator.aclose()
            finally:
                await self._close_source()

    async def _close_source(self) -> None:
        if self._source_closed:
            return
        self._source_closed = True
        if isinstance(self._source, AsyncIterable):
            iterator = self._source_iterator
            if iterator is None:
                iterator = self._source
            if close := getattr(iterator, "aclose", None):
                await close()
        else:
            await run_sync(self._close_sync_source)()

    def _close_sync_source(self) -> None:
        iterator = self._source_iterator
        if iterator is None:
            iterator = self._source
        if close := getattr(iterator, "close", None):
            close()


async def _aclose_source(iterator: Any, failure: Any) -> None:
    """Close ``iterator``; after ``failure`` of its iteration, as a note to it."""
    if close := getattr(iterator, "aclose", None):
        try:
            await close()
        except Exception as exc:
            if failure is None:
                raise
            # The stream raises the iteration's error; closing after it is
            # the secondary failure.
            failure.add_note(f"Closing the source also failed: {exc!r}")


class StreamingResponse(_StreamingResponse):
    """
    Items rendered one at a time, newline-delimited (``application/x-ndjson``).

    ``items`` is an async iterable of data to render (an async generator
    that awaits other services, ``(serializer(obj).data async for obj in
    queryset)``), or a synchronous iterable, which is consumed in the
    request's thread ``chunk_size`` items at a time. A generator expression
    over a queryset is not one: Python iterates the queryset where the
    expression is written, on the event loop; use a generator function or
    ``map()``. Each item is rendered with ``renderer``, DRF's
    ``JSONRenderer`` unless another is given.
    """

    media_type = "application/x-ndjson"
    chunk_size = 100

    def __init__(
        self,
        items: AsyncIterable[Any] | Iterable[Any],
        *,
        renderer: renderers.BaseRenderer | None = None,
        chunk_size: int | None = None,
        **kwargs: Any,
    ) -> None:
        self.renderer = renderers.JSONRenderer() if renderer is None else renderer
        require_sync_hooks(self.renderer, "render")
        media_type = self.renderer.media_type or ""
        if not (
            media_type == "application/json"
            or (media_type.startswith("application/") and media_type.endswith("+json"))
        ):
            raise ValueError("StreamingResponse requires a JSON renderer")
        if chunk_size is not None:
            self.chunk_size = chunk_size
        if type(self.chunk_size) is not int or self.chunk_size <= 0:
            raise ValueError("chunk_size must be a positive integer")
        kwargs.setdefault("content_type", self.media_type)
        super().__init__(items, **kwargs)

    async def _stream(self, items: Any) -> AsyncGenerator[bytes, None]:
        try:
            async with contextlib.aclosing(self._rendered(items)) as chunks:
                async for rendered in chunks:
                    yield rendered + b"\n"
        finally:
            await self._close_source()

    async def _rendered(self, items: Any) -> AsyncGenerator[Any, None]:
        renderer = self.renderer
        if isinstance(items, AsyncIterable):
            self._source_iterator = aiter(items)
            async for item in self._source_iterator:
                yield await _arender(renderer, item)
            return

        # Synchronous: a generator reading a queryset, say. It runs, and
        # renders, in the thread, one hop per ``chunk_size`` items.
        size = self.chunk_size

        def take() -> Any:
            if self._source_iterator is None:
                self._source_iterator = iter(items)
            chunk = []
            for _ in range(size):
                try:
                    item = next(self._source_iterator)
                except StopIteration:
                    break
                chunk.append(_render_item(renderer, item))
            return chunk

        while chunk := await run_sync(take)():
            for rendered in chunk:
                yield rendered


class StreamingArrayResponse(StreamingResponse):
    """The items of :class:`StreamingResponse` as one JSON array."""

    media_type = "application/json"

    async def _stream(self, items: Any) -> AsyncGenerator[bytes, None]:
        try:
            async with contextlib.aclosing(self._rendered(items)) as chunks:
                yield b"["
                first = True
                async for rendered in chunks:
                    yield rendered if first else b"," + rendered
                    first = False
                yield b"]"
        finally:
            await self._close_source()


class ServerSentEvent:
    """
    One event of a ``text/event-stream``: ``data`` (text, or anything the
    renderer turns into JSON) with the optional ``event``, ``id`` and
    ``retry`` fields of the format.
    """

    __slots__ = ("data", "event", "id", "retry")

    def __init__(
        self,
        data: Any,
        *,
        event: str | None = None,
        id: str | int | None = None,
        retry: int | None = None,
    ) -> None:
        for name, value in (("event", event), ("id", id)):
            if value is None:
                continue
            if not isinstance(value, str) and not (name == "id" and type(value) is int):
                raise ValueError(
                    f"{name} must be a string" + (" or integer" if name == "id" else "")
                )
            if any(char in str(value) for char in "\r\n\x00"):
                raise ValueError(f"{name} must not contain CR, LF or NUL")
        if retry is not None and (type(retry) is not int or retry < 0):
            raise ValueError("retry must be a nonnegative integer")
        self.data = data
        self.event = event
        self.id = id
        self.retry = retry

    def __repr__(self) -> str:
        return f"<ServerSentEvent event={self.event!r} id={self.id!r}>"


class EventStreamResponse(_StreamingResponse):
    """
    Server-sent events from an async iterable.

    Every element is a :class:`ServerSentEvent`, or data for one. A
    ``keepalive`` in seconds sends a comment line when the iterable has
    produced nothing for that long, which keeps proxies from closing an
    idle stream. The response is ``text/event-stream`` with
    ``Cache-Control: no-cache``; the iteration ends when the iterable does
    or when the client disconnects, at which point Django cancels it and
    the generator's ``finally`` blocks run.
    """

    media_type = "text/event-stream"

    def __init__(
        self,
        events: AsyncIterable[Any],
        *,
        renderer: renderers.BaseRenderer | None = None,
        keepalive: float | None = None,
        headers: Mapping[str, str] | None = None,
        **kwargs: Any,
    ) -> None:
        self.renderer = renderers.JSONRenderer() if renderer is None else renderer
        require_sync_hooks(self.renderer, "render")
        if keepalive is not None and (
            type(keepalive) not in (int, float)
            or not math.isfinite(keepalive)
            or keepalive <= 0
        ):
            raise ValueError("keepalive must be a finite positive number")
        self.keepalive = keepalive
        kwargs.setdefault("content_type", self.media_type)
        try:
            iterator = aiter(events)
        except TypeError:
            raise TypeError(
                "EventStreamResponse takes an async iterable of events; events "
                "arrive over time, a synchronous iterable would be sent whole."
            ) from None
        super().__init__(
            iterator,
            headers={"Cache-Control": "no-cache", **(headers or {})},
            **kwargs,
        )

    async def _stream(self, iterator: Any) -> AsyncGenerator[bytes, None]:
        events = iterator if self.keepalive is None else self._with_keepalive(iterator)
        try:
            async for item in events:
                if item is _KEEPALIVE:
                    yield b":\n\n"
                    continue
                event = (
                    item if isinstance(item, ServerSentEvent) else ServerSentEvent(item)
                )
                yield await self._encode(event)
        finally:
            try:
                if events is not iterator:
                    await events.aclose()
            finally:
                await self._close_source()

    async def _with_keepalive(self, iterator: Any) -> AsyncGenerator[Any, None]:
        # One task runs the iterable, from its first step to its close, so
        # that what the producer binds to its task (context variables,
        # django-async-backend's connections) sees every step. The client
        # asks for each event (the producer does not run ahead); waiting for
        # it times out into a keepalive without cancelling the step, and a
        # disconnect cancels the step in progress.
        loop = asyncio.get_running_loop()
        wanted: asyncio.Queue[None] = asyncio.Queue()
        results: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

        async def produce() -> None:
            failure = None
            try:
                while True:
                    await wanted.get()
                    try:
                        item = await anext(iterator)
                    except StopAsyncIteration:
                        results.put_nowait(("end", None))
                        return
                    except Exception as exc:  # noqa: BLE001 -- raised again in the stream
                        failure = exc
                        results.put_nowait(("error", exc))
                        return
                    results.put_nowait(("item", item))
            finally:
                # The task that iterated the source closes it; the response
                # must not close it again from another task.
                self._source_closed = True
                await _aclose_source(iterator, failure)

        producer = loop.create_task(produce(), context=contextvars.copy_context())
        pending = None
        try:
            while True:
                wanted.put_nowait(None)
                pending = loop.create_task(results.get())
                while not pending.done():
                    await asyncio.wait(
                        {pending, producer},
                        timeout=self.keepalive,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if not pending.done():
                        if producer.done():
                            producer.result()  # the producer failed outside a step
                        yield _KEEPALIVE
                kind, value = pending.result()
                pending = None
                if kind == "end":
                    return
                if kind == "error":
                    raise value
                yield value
        finally:
            for task in (pending, producer):
                if task is not None and not task.done():
                    task.cancel()
            for task in (pending, producer):
                if task is not None:
                    with contextlib.suppress(
                        asyncio.CancelledError, StopAsyncIteration
                    ):
                        await task

    async def _encode(self, event: Any) -> bytes:
        lines = []
        if event.event is not None:
            lines.append(f"event: {event.event}")
        if event.id is not None:
            lines.append(f"id: {event.id}")
        if event.retry is not None:
            lines.append(f"retry: {event.retry}")
        data = event.data
        if isinstance(data, bytes):
            text = data.decode()
        elif isinstance(data, str):
            text = data
        else:
            text = (await _arender(self.renderer, data)).decode()
        lines.extend(
            f"data: {line}"
            for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        )
        return ("\n".join(lines) + "\n\n").encode()


_KEEPALIVE = object()
