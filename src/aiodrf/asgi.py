"""Django ASGI construction with optional lifespan and request thread reuse."""

from __future__ import annotations

import asyncio
import itertools
import threading
from collections import deque
from collections.abc import AsyncGenerator, Awaitable, Callable
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import asynccontextmanager, nullcontext
from typing import Any

from asgiref.sync import SyncToAsync, ThreadSensitiveContext
from django.conf import settings
from django.core.asgi import get_asgi_application as django_application
from django.core.exceptions import ImproperlyConfigured
from django.core.handlers.asgi import ASGIHandler

from aiodrf.settings import aiodrf_settings

__all__ = ["get_asgi_application"]
_UNSET = object()
_THREAD_STATE_KEY = "aiodrf.request_threads"


def get_asgi_application(*, lifespan: Any = _UNSET) -> Callable[..., Awaitable[None]]:
    """Use aiodrf-asgi-lifespan only when a lifespan feature is selected."""
    application = django_application()
    idle_threads = aiodrf_settings.REQUEST_THREADS
    factory = (
        getattr(settings, "DJANGO_LIFESPAN", None) if lifespan is _UNSET else lifespan
    )
    if factory is None and idle_threads is None:
        return application
    try:
        from aiodrf_asgi_lifespan import asgi
        from aiodrf_asgi_lifespan import settings as lifespan_settings
    except ModuleNotFoundError as exc:
        if exc.name != "aiodrf_asgi_lifespan":
            raise
        raise ImproperlyConfigured(
            "Install django-aiodrf[lifespan] to use DJANGO_LIFESPAN or REQUEST_THREADS."
        ) from exc
    factory = lifespan_settings.resolve_lifespan(factory)
    if idle_threads is None:
        return asgi.LifespanApplication(application, lifespan=factory)
    return _ThreadLifespanApplication(_ThreadKeepingHandler(), idle_threads, factory)


class _ThreadLifespanApplication:
    """Publish an independent request pool in each server lifespan's state."""

    def __init__(self, application: ASGIHandler, limit: int, factory: Any) -> None:
        self.application = application
        self.limit = limit
        self.factory = factory

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "lifespan":
            await self.application(scope, receive, send)
            return
        from aiodrf_asgi_lifespan.asgi import LifespanApplication

        state = scope.get("state")
        if state is None or _THREAD_STATE_KEY in state:
            await receive()
            await send(
                {
                    "type": "lifespan.startup.failed",
                    "message": "REQUEST_THREADS requires an unused ASGI scope['state'].",
                }
            )
            return
        threads = _RequestThreads(self.limit)
        state[_THREAD_STATE_KEY] = threads
        wrapper = LifespanApplication(
            self.application, lifespan=_thread_lifespan(threads, self.factory)
        )
        try:
            await wrapper(scope, receive, send)
        finally:
            state.pop(_THREAD_STATE_KEY, None)
            await threads.aclose()


def _thread_lifespan(threads: _RequestThreads, factory: Any) -> Any:
    @asynccontextmanager
    async def lifespan() -> AsyncGenerator[Any]:
        async with factory() if factory is not None else nullcontext(None) as resource:
            try:
                yield resource
            finally:
                # Worker code can still refer to the user's resources.
                # Finish it before closing the enclosing resource context.
                await threads.aclose()

    return lifespan


class _RequestThreads:
    """
    Single-thread executors lent to one request at a time.

    asgiref runs a request's synchronous code (Django's signals, ORM calls,
    ``sync_to_async``) in one thread of its own, which Django's handler starts
    for the request and joins, in another thread, after it. Lent executors
    keep that rule — one request, one thread, no other request in it; a
    request aborted while its code still runs does not lend its thread until
    that code ran. At most ``idle_limit`` idle executors are kept; threads
    carry thread-local state (database connections) across requests, as a
    threaded WSGI server's do.
    """

    def __init__(self, idle_limit: int) -> None:
        self.idle_limit = idle_limit
        self._idle: deque[_RequestExecutor] = deque()
        self._lock = threading.Lock()
        self._numbers = itertools.count(1)
        self._executors: set[_RequestExecutor] = set()
        self._closed = False

    def lend(self) -> _RequestExecutor:
        with self._lock:
            if self._closed:
                raise RuntimeError("The request thread lifespan is closed.")
            if self._idle:
                return self._idle.pop()
            executor = _RequestExecutor(f"aiodrf-request-{next(self._numbers)}")
            self._executors.add(executor)
            return executor

    def give_back(self, executor: _RequestExecutor) -> None:
        """
        Keep ``executor`` for another request: at once when its work is done;
        else once it is (one worker, first in first out: a marker queued now
        runs after that work).
        """
        if executor.idle():
            self._returned(executor)
            return
        try:
            marker = executor.submit(int)
        except RuntimeError:  # the interpreter is shutting down
            return
        marker.add_done_callback(lambda _: self._returned(executor))

    def _returned(self, executor: _RequestExecutor) -> None:
        with self._lock:
            if not self._closed and len(self._idle) < self.idle_limit:
                self._idle.append(executor)
                return
            self._executors.discard(executor)
        executor.shutdown(wait=False)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            executors = tuple(self._executors)
            self._executors.clear()
            self._idle.clear()
        # Joining must not hold the publication lock: a worker's final
        # callback can still be returning its executor to this pool.
        for executor in executors:
            executor.shutdown(wait=True)

    async def aclose(self) -> None:
        pending = asyncio.create_task(asyncio.to_thread(self.close))
        cancelled = False
        while not pending.done():
            try:
                await asyncio.shield(pending)
            except asyncio.CancelledError:
                cancelled = True
        pending.result()
        if cancelled:
            raise asyncio.CancelledError

    def idle_count(self) -> int:
        return len(self._idle)


class _RequestExecutor(ThreadPoolExecutor):
    """One worker, knowing whether work submitted to it is still to finish."""

    def __init__(self, name: str) -> None:
        super().__init__(max_workers=1, thread_name_prefix=name)
        self._pending = 0
        self._pending_lock = threading.Lock()

    def submit(  # type: ignore[override]  # Executor.submit's ParamSpec
        self, fn: Callable[..., object], /, *args: object, **kwargs: object
    ) -> Future[object]:
        with self._pending_lock:
            self._pending += 1
        try:
            future = super().submit(fn, *args, **kwargs)
        except BaseException:
            self._finished(None)
            raise
        # Registered before the caller's callbacks: asyncio learns of the
        # result after the count dropped.
        future.add_done_callback(self._finished)
        return future

    def _finished(self, _future: object) -> None:
        with self._pending_lock:
            self._pending -= 1

    def idle(self) -> bool:
        return not self._pending


class _LentThreadContext(ThreadSensitiveContext):
    """asgiref's per-request context, with its executor lent by ``threads``."""

    def __init__(self, threads: _RequestThreads) -> None:
        super().__init__()
        self.threads = threads

    async def __aenter__(self) -> _LentThreadContext:
        await super().__aenter__()
        if self.token:  # the outermost context of the request
            SyncToAsync.context_to_thread_executor[self] = self.threads.lend()
        return self

    async def __aexit__(self, exc: object, value: object, tb: object) -> None:
        if not self.token:
            return
        executor = SyncToAsync.context_to_thread_executor.pop(self, None)
        SyncToAsync.thread_sensitive_context.reset(self.token)
        if isinstance(executor, _RequestExecutor):
            # A disconnect cancels the request, not the synchronous code it
            # awaited; code it started without awaiting finds no executor for
            # this context any more.
            self.threads.give_back(executor)


class _ThreadKeepingHandler(ASGIHandler):
    """Django's ``ASGIHandler`` whose requests borrow ``request_threads``."""

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[Any]],
        send: Callable[[Any], Awaitable[None]],
    ) -> None:
        # Django's ``__call__``, with the lent context.
        if scope["type"] != "http":
            raise ValueError(
                "Django can only handle ASGI/HTTP connections, not %s." % scope["type"]
            )
        threads = (scope.get("state") or {}).get(_THREAD_STATE_KEY)
        if not isinstance(threads, _RequestThreads):
            raise ImproperlyConfigured(
                "REQUEST_THREADS requires ASGI lifespan startup and scope['state']."
            )
        async with _LentThreadContext(threads):
            await self.handle(scope, receive, send)
