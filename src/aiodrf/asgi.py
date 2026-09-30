"""Django's ASGI application with signals and an optional resource context.

``AIODRF['LIFESPAN']`` names a zero-argument async context manager factory.
Its yielded value is available through ``get_lifespan_state(request, Type)``.
The factory runs once per lifespan connection, on the server's event loop;
the wrapper and settings never retain the resource across connections.

``AIODRF['REQUEST_THREADS']`` (opt-in) keeps the threads that run requests'
synchronous code for later requests instead of starting and joining two
threads per request, as Django's handler does through asgiref.
"""

from __future__ import annotations

import itertools
import threading
import traceback
from collections import deque
from collections.abc import Awaitable, Callable
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from dataclasses import dataclass
from inspect import isasyncgen, iscoroutine
from typing import TYPE_CHECKING, Any, cast, overload

import django
from asgiref.sync import SyncToAsync, ThreadSensitiveContext
from asgiref.typing import ASGIReceiveCallable, ASGISendCallable, Scope
from django.core.asgi import get_asgi_application as _django_application
from django.core.exceptions import ImproperlyConfigured
from django.core.handlers.asgi import ASGIHandler
from django.dispatch import Signal

from aiodrf.settings import aiodrf_settings, resolve_lifespan
from aiodrf.signals import asgi_shutdown, asgi_startup

if TYPE_CHECKING:
    from django.http import HttpRequest
    from rest_framework.request import Request

__all__ = [
    "LifespanApplication",
    "LifespanFactory",
    "get_asgi_application",
    "get_lifespan_state",
]

type LifespanFactory[T] = Callable[[], AbstractAsyncContextManager[T]]

_UNSET = object()
_STATE_KEY = "aiodrf.lifespan"


@dataclass(slots=True)
class _LifespanState:
    value: object
    active: bool = True

    def invalidate(self, state: dict[str, object]) -> None:
        self.active = False
        # Request scopes shallow-copy this marker. Invalidate access and drop
        # its reference so retained requests do not keep a closed pool alive.
        self.value = None
        if state.get(_STATE_KEY) is self:
            del state[_STATE_KEY]


def get_lifespan_state[T](request: HttpRequest | Request, expected_type: type[T]) -> T:
    """Return the live, typed resource; never create one on first access.

    ``expected_type`` must support ``isinstance`` (a dataclass is suitable;
    ``TypedDict`` is not). Both Django and DRF requests are accepted.
    """
    # ``state`` is optional in the ASGI specification, and may be ``None``.
    state = ((getattr(request, "scope", None) or {}).get("state") or {}).get(_STATE_KEY)
    if not isinstance(state, _LifespanState) or not state.active:
        raise ImproperlyConfigured(
            "No active aiodrf lifespan state. Use aiodrf's ASGI application with "
            "a lifespan context manager and a server that propagates scope['state']."
        )
    if not isinstance(state.value, expected_type):
        raise ImproperlyConfigured(
            f"Lifespan state must be {expected_type.__qualname__}, "
            f"not {type(state.value).__qualname__}."
        )
    return state.value


async def _send_signal(signal: Signal, sender: object, scope: Scope) -> None:
    # Robust dispatch waits for every receiver. A first failure must not
    # leave other resource-owning receivers running after lifespan returns.
    responses = await signal.asend_robust(sender=sender, scope=scope)
    for _receiver, result in responses:
        if isinstance(result, Exception):
            raise result


async def _expect_message(receive: ASGIReceiveCallable, phase: str) -> None:
    message = await receive()
    if message["type"] != f"lifespan.{phase}":
        raise RuntimeError(f"Expected lifespan.{phase}, received {message['type']!r}.")


class LifespanApplication:
    """ASGI application answering ``lifespan`` and delegating the rest."""

    def __init__(
        self,
        application: Callable[..., Awaitable[None]],
        *,
        lifespan: LifespanFactory[object] | None = None,
    ) -> None:
        self.application = application
        self.lifespan = resolve_lifespan(lifespan)

    async def __call__(
        self, scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable
    ) -> None:
        if scope["type"] != "lifespan":
            await self.application(scope, receive, send)
            return
        if self.lifespan is not None:
            await self._managed_lifespan(scope, receive, send)
            return
        # Preserve the signal-only protocol for existing applications.
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                try:
                    await _send_signal(asgi_startup, type(self), scope)
                except Exception:  # noqa: BLE001 -- reported to the server, whatever it is
                    await send(
                        {
                            "type": "lifespan.startup.failed",
                            "message": traceback.format_exc(),
                        }
                    )
                    return
                else:
                    await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                try:
                    await _send_signal(asgi_shutdown, type(self), scope)
                except Exception:  # noqa: BLE001
                    await send(
                        {
                            "type": "lifespan.shutdown.failed",
                            "message": traceback.format_exc(),
                        }
                    )
                else:
                    await send({"type": "lifespan.shutdown.complete"})
                return

    async def _open_lifespan(self, stack: AsyncExitStack, scope: Scope) -> None:
        context = self.lifespan()
        if not isinstance(context, AbstractAsyncContextManager):
            # A malformed sync factory can return a coroutine/generator.
            # Do not execute it or leak an unawaited-coroutine warning.
            if iscoroutine(context):
                context.close()
            elif isasyncgen(context):
                await context.aclose()
            raise ImproperlyConfigured(
                "LIFESPAN must return an async context manager. "
                "Use @contextlib.asynccontextmanager."
            )
        resource = await stack.enter_async_context(context)
        if resource is None:
            return
        state = scope.get("state")
        if state is None:
            raise ImproperlyConfigured(
                "A lifespan yielding resources requires server support for scope['state']."
            )
        if _STATE_KEY in state:
            raise ImproperlyConfigured(
                "scope['state']['aiodrf.lifespan'] is already in use."
            )
        published = _LifespanState(resource)
        state[_STATE_KEY] = published
        # Remove access before __aexit__ starts closing the resource. All
        # shallow request copies share the same invalidation marker.
        stack.callback(published.invalidate, state)

    async def _managed_lifespan(
        self,
        scope: Scope,
        receive: ASGIReceiveCallable,
        send: Callable[[Any], Awaitable[None]],
    ) -> None:
        phase = "startup"
        stack = AsyncExitStack()
        try:
            try:
                await _expect_message(receive, "startup")
                await self._open_lifespan(stack, scope)
                await _send_signal(asgi_startup, type(self), scope)
                await send({"type": "lifespan.startup.complete"})
                phase = "shutdown"
                await _expect_message(receive, "shutdown")
                await _send_signal(asgi_shutdown, type(self), scope)
            except BaseException as exc:
                try:
                    await stack.__aexit__(type(exc), exc, exc.__traceback__)
                finally:
                    # Cleanup cannot suppress cancellation or interpreter exit.
                    if not isinstance(exc, Exception):
                        raise exc  # noqa: TRY201 -- preserve cancellation if cleanup raised
                # Suppressing an exception inside the user's context must not
                # turn a failed startup/shutdown into a success response.
                raise
            else:
                await stack.aclose()
        except Exception:  # noqa: BLE001 -- the ASGI protocol reports failures
            await send(
                {"type": f"lifespan.{phase}.failed", "message": traceback.format_exc()}
            )
        else:
            await send({"type": "lifespan.shutdown.complete"})


@overload
def get_asgi_application() -> LifespanApplication: ...


@overload
def get_asgi_application(
    *, lifespan: LifespanFactory[object] | None
) -> LifespanApplication: ...


def get_asgi_application(*, lifespan: object = _UNSET) -> LifespanApplication:
    """Wrap Django's application; explicit ``lifespan=None`` disables the setting."""
    # What Django's ``get_asgi_application`` does first.
    django.setup(set_prefix=False)
    idle_threads = aiodrf_settings.REQUEST_THREADS
    application: Callable[..., Awaitable[None]] = (
        _django_application()
        if idle_threads is None
        else _ThreadKeepingHandler(_RequestThreads(idle_threads))
    )
    if lifespan is _UNSET:
        lifespan = aiodrf_settings.LIFESPAN
    return LifespanApplication(
        application, lifespan=cast("LifespanFactory[object] | None", lifespan)
    )


# -- Request threads -------------------------------------------------------------


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

    def lend(self) -> _RequestExecutor:
        try:
            return self._idle.pop()
        except IndexError:
            return _RequestExecutor(f"aiodrf-request-{next(self._numbers)}")

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
            if len(self._idle) < self.idle_limit:
                self._idle.append(executor)
                return
        executor.shutdown(wait=False)

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

    def __init__(self, request_threads: _RequestThreads) -> None:
        super().__init__()
        self.request_threads = request_threads

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
        async with _LentThreadContext(self.request_threads):
            await self.handle(scope, receive, send)
