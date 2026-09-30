"""Saving: ``save``, the default ``create``/``update`` call and ``ATOMIC_SAVE``."""

import contextlib
import functools
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.db import DEFAULT_DB_ALIAS, connections, models, router, transaction
from rest_framework import serializers
from rest_framework.serializers import BaseSerializer

from aiodrf.aio._common import NEEDS_AWAIT, _bridged, _sync_member
from aiodrf.settings import aiodrf_settings
from aiodrf.utils import (
    Impl,
    invoke,
    resolve_pair,
)


async def save[T](serializer: BaseSerializer[T], **kwargs: Any) -> T:
    """Async counterpart of ``serializer.save()`` for any DRF serializer."""
    # The pair's members are the serializer's own; DRF's stubs have neither.
    pair: Any = serializer
    impl = resolve_pair(serializer, "save", "asave")
    if impl is Impl.ASYNC:
        return await invoke(pair.asave, **kwargs)
    if impl is Impl.SYNC_IS_ASYNC:
        return await invoke(pair.save, **kwargs)
    if impl is Impl.SYNC:
        return await invoke(_own_save, serializer, **kwargs)
    return await default_save(serializer, **kwargs)


def try_save(serializer: Any, **kwargs: Any) -> Any:
    """
    The synchronous half of :func:`save`: ``serializer.save()`` in one
    transaction (``ATOMIC_SAVE``), or :data:`NEEDS_AWAIT` when saving is
    implemented with ``asave``, ``acreate`` or ``aupdate``.
    """
    impl = resolve_pair(serializer, "save", "asave")
    if impl in (Impl.ASYNC, Impl.SYNC_IS_ASYNC):
        return NEEDS_AWAIT
    if impl is Impl.SYNC:
        return _own_save(serializer, **kwargs)
    if _async_write(serializer) is not None:
        return NEEDS_AWAIT
    return _atomic(serializer, _sync_member(serializer, "save", "asave"))(**kwargs)


def _own_save(serializer: Any, **kwargs: Any) -> Any:
    # A ``save()`` written for DRF owns its transaction.
    check_drf_save(serializer, f"{type(serializer).__qualname__}.save()", "asave")
    return serializer.save(**kwargs)


def check_drf_save(serializer: Any, caller: str, async_caller: str) -> None:
    """
    Raise ImproperlyConfigured when ``caller``, code written for DRF, is about
    to call DRF's ``save()`` on a serializer whose ``create()`` or
    ``update()`` must be awaited: DRF would skip an ``acreate()`` and never
    await an ``async def create()``.
    """
    if _bridged(type(serializer), "save"):
        return
    name = _async_write(serializer)
    if name is not None:
        raise ImproperlyConfigured(
            f"{caller} calls DRF's synchronous save(), which cannot run `{name}()` "
            f"of {type(serializer).__qualname__}. Override `async def {async_caller}()` "
            "instead, or inherit from an aiodrf serializer, whose save() awaits it."
        )


def _async_write(serializer: Any) -> str | None:
    """The name of the write of ``serializer`` that must be awaited, or None."""
    write = (
        ("update", "aupdate")
        if serializer.instance is not None
        else ("create", "acreate")
    )
    writers = [serializer]
    if isinstance(serializer, serializers.ListSerializer):
        writers.append(serializer.child)
    for writer in writers:
        impl = resolve_pair(writer, *write)
        if impl is Impl.ASYNC:
            return write[1]
        if impl is Impl.SYNC_IS_ASYNC:
            return write[0]
    return None


async def default_save[T](serializer: BaseSerializer[T], **kwargs: Any) -> T:
    """The implementation behind :func:`save` and ``asave()``."""
    many = isinstance(serializer, serializers.ListSerializer)
    if not many:
        assert hasattr(serializer, "_errors"), (  # noqa: S101 -- as in DRF, like those below
            "You must call `.is_valid()` before calling `.save()`."
        )

        assert not serializer.errors, (  # noqa: S101
            "You cannot call `.save()` on a serializer with invalid data."
        )

    # Guard against incorrect use of `serializer.save(commit=False)`
    assert "commit" not in kwargs, (  # noqa: S101
        "'commit' is not a valid keyword argument to the 'save()' method. "
        "If you need to access data before committing to the database then "
        "inspect 'serializer.validated_data' instead. "
        "You can also pass additional keyword arguments to 'save()' if you "
        "need to set extra attributes on the saved model instance. "
        "For example: 'serializer.save(owner=request.user)'.'"
    )

    if not many:
        assert not hasattr(serializer, "_data"), (  # noqa: S101
            "You cannot call `.save()` after accessing `serializer.data`."
            "If you need to access data before committing to the database then "
            "inspect 'serializer.validated_data' instead. "
        )
        validated_data: Any = {**serializer.validated_data, **kwargs}
    else:
        validated_data = [{**attrs, **kwargs} for attrs in serializer.validated_data]

    if serializer.instance is not None:
        serializer.instance = await _acall_write(
            serializer, "update", "aupdate", serializer.instance, validated_data
        )
        assert serializer.instance is not None, (  # noqa: S101
            "`update()` did not return an object instance."
        )
    else:
        serializer.instance = await _acall_write(
            serializer, "create", "acreate", validated_data
        )
        assert serializer.instance is not None, (  # noqa: S101
            "`create()` did not return an object instance."
        )

    return serializer.instance


async def _acall_write(
    serializer: Any, sync_name: str, async_name: str, *args: Any
) -> Any:
    impl = resolve_pair(serializer, sync_name, async_name)
    if impl is Impl.ASYNC:
        return await invoke(getattr(serializer, async_name), *args)
    if impl is Impl.SYNC_IS_ASYNC:
        return await invoke(getattr(serializer, sync_name), *args)
    if (
        isinstance(serializer, serializers.ListSerializer)
        and impl is Impl.BASE
        and sync_name == "create"
        and resolve_pair(serializer.child, "create", "acreate")
        in (Impl.ASYNC, Impl.SYNC_IS_ASYNC)
    ):
        # Mirrors ``ListSerializer.create`` for children with async creation.
        return [
            await _acall_write(serializer.child, "create", "acreate", attrs)
            for attrs in args[0]
        ]
    return await invoke(_atomic(serializer, getattr(serializer, sync_name)), *args)


#: Database vendor -> ``factory(using)``, the context manager ``ATOMIC_SAVE``
#: enters instead of ``transaction.atomic`` where that is not a transaction.
#: django-mongodb-backend's is a no-op; ``aiodrf.contrib.mongodb`` registers
#: the backend's own.
_ATOMIC_FACTORIES: dict[str, Callable[[str], AbstractContextManager[Any]]] = {}


def _atomic(serializer: Any, func: Callable[..., Any]) -> Callable[..., Any]:
    """
    Run a write in one transaction. Without it, the separate queries of
    ``ModelSerializer.create`` (the insert and each many-to-many ``set``)
    could partially apply; async views cannot use ``ATOMIC_REQUESTS``.
    """
    if not aiodrf_settings.ATOMIC_SAVE:
        return func

    # Everything below runs when the save does, in the worker thread: the
    # routers are the project's code, and a factory may ask the database
    # whether it can have a transaction.
    @functools.wraps(func)
    def atomic_save(*args: Any, **kwargs: Any) -> Any:
        with contextlib.ExitStack() as blocks:
            for using in _write_aliases(serializer):
                # Without a registration, no connection object for this thread either.
                factory = (
                    _ATOMIC_FACTORIES.get(connections[using].vendor)
                    if _ATOMIC_FACTORIES
                    else None
                )
                blocks.enter_context(
                    transaction.atomic(using=using)
                    if factory is None
                    else factory(using)
                )
            return func(*args, **kwargs)

    return atomic_save


def _write_aliases(serializer: Any) -> list[str]:
    """
    The databases Django's routers choose for the writes of ``serializer``:
    ``Model.save()`` routes with the instance as a hint, so an update of
    instances loaded from several databases writes to each of them.
    """
    target = (
        serializer.child
        if isinstance(serializer, serializers.ListSerializer)
        else serializer
    )
    model = getattr(getattr(target, "Meta", None), "model", None)
    if model is None:
        return [DEFAULT_DB_ALIAS]
    instance = serializer.instance
    if isinstance(instance, models.Model):
        return [router.db_for_write(model, instance=instance)]
    if isinstance(instance, models.QuerySet):
        # Evaluating it here would query outside the transaction (and fail
        # for ``select_for_update()``): only the objects it already holds.
        instance = instance._result_cache
    if isinstance(instance, (list, tuple)):
        aliases = {
            router.db_for_write(model, instance=item)
            for item in instance
            if isinstance(item, models.Model)
        }
        if aliases:
            return sorted(aliases)
    return [router.db_for_write(model)]
