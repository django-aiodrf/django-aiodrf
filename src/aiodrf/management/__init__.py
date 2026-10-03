"""
Management commands written as coroutines.

Django runs commands synchronously and has no async variant (ticket #31793).
:class:`AsyncCommand` keeps Django's machinery (argument parsing, system and
migration checks, output, ``call_command``) and runs only the handler on an
event loop. The loop is driven with ``async_to_sync`` rather than
``asyncio.run()``: work the async ORM hands to ``sync_to_async`` then runs in
the command's own thread, on the connections Django closes after the command
and inside a test's transaction, instead of in asgiref's executor thread.
"""

from __future__ import annotations

import asyncio
import inspect
import signal
import threading
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from types import MethodType
from typing import Any

from asgiref.sync import async_to_sync
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured, SynchronousOnlyOperation
from django.core.management import call_command
from django.core.management.base import BaseCommand

from aiodrf.utils import Impl, bridge_base, resolve_pair, run_sync

__all__ = ["AsyncCommand", "acall_command"]

_UNSET: Any = object()


class _Cancellation:
    """
    Cancel the command's task on SIGINT or SIGTERM.

    The loop runs in another thread, so a signal would only interrupt the
    waiting main thread while the coroutine carried on. This does for both
    signals what ``asyncio.Runner`` does for SIGINT.
    """

    # Only Python's defaults are replaced: a project's own handler, or
    # SIG_IGN (nohup, background jobs), keeps working.
    DEFAULTS = {
        signal.SIGINT: signal.default_int_handler,
        signal.SIGTERM: signal.SIG_DFL,
    }

    def __init__(self) -> None:
        self.previous: dict[int, Any] = {}
        self.received: int | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.task: asyncio.Task[Any] | None = None

    def __enter__(self) -> _Cancellation:
        # signal.signal() works in the main thread only.
        if threading.current_thread() is threading.main_thread():
            for signum, default in self.DEFAULTS.items():
                if signal.getsignal(signum) is default:
                    self.previous[signum] = signal.signal(signum, self.cancel)
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.restore()

    def restore(self) -> None:
        # Swapped first: the signal handler restores too, possibly while
        # __exit__ is iterating.
        previous, self.previous = self.previous, {}
        for signum, handler in previous.items():
            signal.signal(signum, handler)

    def cancel(self, signum: int, frame: object) -> None:
        # signal.signal supplies both arguments; cancellation does not use frame.
        self.received = signum
        # A second signal gets the previous handler: an immediate stop.
        self.restore()
        # Once the handler has finished, possibly with its loop closed,
        # there is nothing to cancel: reraise() still raises for the signal.
        if (
            self.task is not None
            and self.loop is not None
            and not self.loop.is_closed()
            and not self.task.done()
        ):
            self.loop.call_soon_threadsafe(self.task.cancel)

    async def started(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.task = asyncio.current_task()
        if self.received is not None:
            # Signalled before there was a task to cancel.
            raise asyncio.CancelledError

    def reraise(self) -> None:
        # SIGINT: what the default handler and asyncio.run() raise. SIGTERM:
        # the conventional status of a process it terminated, as an exit that
        # still runs Django's cleanup (run_from_argv closes the connections).
        if self.received == signal.SIGINT:
            raise KeyboardInterrupt
        if self.received == signal.SIGTERM:
            raise SystemExit(128 + signal.SIGTERM)


@bridge_base
class AsyncCommand(BaseCommand):
    """
    A management command whose handler is ``async def ahandle()``, or
    ``async def handle()``.

    Everything but the handler is Django's and runs synchronously first. The
    handler runs on an event loop of its own, and its return value is output
    as ``handle()``'s is. SIGINT cancels it and raises ``KeyboardInterrupt``;
    SIGTERM cancels it and exits with status 143.

    With ``lifespan = True``, ``DJANGO_LIFESPAN`` is entered around the
    handler, on its loop, and its value is ``self.get_lifespan_state(Type)``.
    The ``asgi_startup`` and ``asgi_shutdown`` signals are not sent: their
    receivers expect an ASGI scope.
    """

    lifespan = False

    _lifespan_state: Any = _UNSET

    def execute(self, *args: Any, **options: Any) -> str | None:
        if resolve_pair(type(self), "handle", "ahandle") is Impl.SYNC_IS_ASYNC:
            # BaseCommand.execute() calls self.handle(), here a coroutine
            # function whose coroutine it would write out unawaited.
            self.handle = MethodType(AsyncCommand.handle, self)  # type: ignore[method-assign]
        return super().execute(*args, **options)

    def handle(self, *args: Any, **options: Any) -> str | None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise SynchronousOnlyOperation(
                f"{type(self).__qualname__} cannot run while an event loop is running in "
                "this thread. Use 'await aiodrf.management.acall_command(...)'."
            )
        handler: Callable[..., Awaitable[str | None]]
        if resolve_pair(type(self), "handle", "ahandle") is Impl.SYNC_IS_ASYNC:
            # The class's coroutine function; execute() shadowed it on the instance.
            handler = MethodType(type(self).handle, self)
        else:
            handler = self.ahandle
        with _Cancellation() as cancellation:
            try:
                return async_to_sync(self._run)(cancellation, handler, args, options)
            finally:
                # A signal decides the outcome: the cancellation, an error
                # raised while unwinding (kept as the context), or a result
                # returned as it arrived.
                cancellation.reraise()

    async def ahandle(self, *args: Any, **options: Any) -> str | None:
        # The next handle() in the MRO: BaseCommand's raises
        # NotImplementedError; django-typer's runs the Typer app, which
        # returns the coroutine of an ``async def`` command. Synchronous code
        # runs in the command's thread.
        result = await run_sync(super().handle)(*args, **options)
        if inspect.isawaitable(result):
            result = await result
        return result

    def get_lifespan_state[T](self, expected_type: type[T]) -> T:
        """Return the lifespan's value while the handler runs; see ``aiodrf.asgi``."""
        value = self._lifespan_state
        if value is _UNSET:
            raise ImproperlyConfigured(
                "No active lifespan state. Set lifespan = True on the command "
                "and configure DJANGO_LIFESPAN."
            )
        if not isinstance(value, expected_type):
            raise ImproperlyConfigured(
                f"Lifespan state must be {expected_type.__qualname__}, "
                f"not {type(value).__qualname__}."
            )
        return value

    async def _run(
        self,
        cancellation: _Cancellation,
        handler: Callable[..., Awaitable[Any]],
        args: tuple[Any, ...],
        options: dict[str, Any],
    ) -> Any:
        await cancellation.started()
        async with AsyncExitStack() as stack:
            if apps.is_installed("django_async_backend"):
                # Native connections belong to the loop that opened them and
                # must be closed before it ends.
                from django_async_backend.db import async_new_connection

                await stack.enter_async_context(async_new_connection())
            if self.lifespan:
                await self._enter_lifespan(stack)
            return await handler(*args, **options)

    async def _enter_lifespan(self, stack: AsyncExitStack) -> None:
        try:
            from aiodrf_asgi_lifespan.settings import get_lifespan_factory
        except ModuleNotFoundError as exc:
            if exc.name != "aiodrf_asgi_lifespan":
                raise
            raise ImproperlyConfigured(
                "Install django-aiodrf[lifespan] for command lifespans."
            ) from exc
        factory = get_lifespan_factory()
        if factory is None:
            raise ImproperlyConfigured(
                f"{type(self).__qualname__}.lifespan is True, but DJANGO_LIFESPAN is not set."
            )
        context = factory()
        if not isinstance(context, AbstractAsyncContextManager):
            if inspect.iscoroutine(context):
                context.close()
            raise ImproperlyConfigured(
                "LIFESPAN must return an async context manager. "
                "Use @contextlib.asynccontextmanager."
            )
        self._lifespan_state = await stack.enter_async_context(context)
        # Access ends before the context starts closing the resource.
        stack.callback(vars(self).pop, "_lifespan_state")


async def acall_command(
    command_name: str | BaseCommand, *args: Any, **options: Any
) -> Any:
    """
    ``call_command()`` for async code.

    It runs in the thread synchronous work goes to, as the async ORM does:
    inside a test's transaction and on the connections Django manages.
    """
    return await run_sync(call_command)(command_name, *args, **options)
