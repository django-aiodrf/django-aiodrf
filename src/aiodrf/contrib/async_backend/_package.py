"""django-async-backend's API, imported once, and what running it needs."""

import asyncio
import contextlib
import warnings
from collections.abc import AsyncGenerator, Callable
from typing import Any

from asgiref.sync import async_to_sync
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.db import router, transaction
from django.db.models.signals import (
    m2m_changed,
    post_delete,
    post_save,
    pre_delete,
    pre_save,
)

from aiodrf.utils import run_sync

with warnings.catch_warnings():
    # django-async-backend 6.1 imports ``RemovedInDjango70Warning``, which
    # Django 6.2 serves from a module ``__getattr__`` that warns. The name
    # still resolves (to ``RemovedInDjango2028Warning``); only this warning
    # is silenced, and only for this import.
    warnings.filterwarnings(
        "ignore",
        message="RemovedInDjango2028Warning should be used instead of RemovedInDjango70Warning",
        category=UserWarning,
    )
    from django_async_backend.db import async_connections, async_new_connection
    from django_async_backend.db.models.query import QuerySet as NativeQuerySet
    from django_async_backend.db.transaction import async_atomic

__all__ = [
    "NativeQuerySet",
    "async_atomic",
    "async_connections",
    "awrite_alias",
    "receivers_in_transaction",
    "require_installed",
    "run_native",
]

# The signals a native write sends, inside its native transaction.
_WRITE_SIGNALS = (pre_save, post_save, pre_delete, post_delete, m2m_changed)


async def awrite_alias(model: Any, **hints: Any) -> Any:
    """
    ``router.db_for_write()``. The project's routers may query (a tenant
    lookup), so they are asked in a worker; without any, only Django's
    default runs, on the loop.
    """
    if not router.routers:
        return router.db_for_write(model, **hints)
    return await run_sync(router.db_for_write)(model, **hints)


def require_installed(owner: Any) -> None:
    """The package's app adds the async members native writes need to every model."""
    if not apps.is_installed("django_async_backend"):
        raise ImproperlyConfigured(
            f"{owner.__module__}.{owner.__qualname__} uses aiodrf.contrib.async_backend, "
            "which needs 'django_async_backend' in INSTALLED_APPS: its app gives every "
            "model the async members (async_objects, async_save, ...) native queries "
            "and writes use."
        )


def run_native(func: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    """
    Run native ORM work for a synchronous caller (DRF's browsable API, a
    synchronous hook calling ``serializer.save()``).

    A native connection belongs to the first task that used it, which is the
    request's; ``async_to_sync`` runs the work in another task, so it gets a
    connection of its own (``async_new_connection``), closed when it ends.
    """
    return async_to_sync(async_new_connection(func))(*args, **kwargs)


@contextlib.asynccontextmanager
async def receivers_in_transaction(using: str) -> AsyncGenerator[None]:
    """
    Django's transaction, on Django's connection, around a native write and
    its native transaction.

    Django sends the write's signals inside the native transaction and runs
    synchronous receivers in a thread, on Django's connection, which is in
    no transaction: what they defer would run at once, before the native
    commit and even when it rolls back (``transaction.on_commit()``
    callbacks, such as django-cleanup's file deletions; django-cacheops'
    invalidations, deferred by its own ``Atomic`` tracking). In Django's
    transaction they wait for it, and it ends after the native one:
    committed, running the callbacks in that thread, or rolled back with
    it. The receivers' own queries run in it too.

    Nothing is done without receivers, or inside a native transaction
    already open: aiodrf's own is already covered, the project's cannot be
    (its commit comes later).
    """
    if async_connections[using].in_atomic_block or not any(
        signal.receivers for signal in _WRITE_SIGNALS
    ):
        yield
        return
    block = transaction.atomic(using=using)
    # Both hops run in the request's sync thread, where Django sends the
    # signals (sync_to_async, thread-sensitive): the transaction is its
    # connection's.
    entry, cancelled = await _to_the_end(block.__enter__)
    entry.result()  # A failed entry has nothing to exit.
    if cancelled is not None:
        # Cancelled while the thread was entering, which it did.
        await _exit(block, cancelled)
        raise cancelled
    try:
        yield
    except BaseException as exc:
        await _exit(block, exc)
        raise
    await _exit(block, None)


async def _exit(block: Any, exc: BaseException | None) -> None:
    """Exit ``block`` for ``exc`` (or none); a cancellation meanwhile is raised after it."""
    exc_info = (
        (None, None, None) if exc is None else (type(exc), exc, exc.__traceback__)
    )
    exit_, cancelled = await _to_the_end(block.__exit__, *exc_info)
    exit_.result()
    if cancelled is not None:
        raise cancelled


async def _to_the_end(
    func: Callable[..., Any], *args: Any
) -> tuple[asyncio.Future[Any], asyncio.CancelledError | None]:
    """
    Run ``func`` in the request's sync thread to its end, even when the
    caller is cancelled meanwhile: ``(finished future, cancellation or None)``.

    Cancelling the caller of a hop stops its wait, not the thread, which
    would still enter or end the transaction, unseen; and it cancels a hop
    not yet started. The hop runs as a task of its own, which cancellations
    of the caller do not reach, and the caller waits for it regardless.
    """
    hop = asyncio.ensure_future(run_sync(func)(*args))
    cancelled = None
    while not hop.done():
        try:
            await asyncio.wait((hop,))
        except asyncio.CancelledError as exc:
            cancelled = exc
    return hop, cancelled
