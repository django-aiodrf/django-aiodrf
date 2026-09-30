"""Optional bounded concurrency for independent async item representation."""

import asyncio
import inspect
import weakref
from collections.abc import Iterable
from itertools import islice
from typing import Any

from django.db.models.manager import BaseManager
from rest_framework.serializers import BaseSerializer, Serializer
from rest_framework.serializers import ListSerializer as DRFListSerializer

from aiodrf import aio
from aiodrf.aio._classify import has_async_representation
from aiodrf.hooks import require_sync_hooks
from aiodrf.serializers import ListSerializer
from aiodrf.utils import run_sync

__all__ = ["ConcurrentListSerializer"]


class ConcurrentListSerializer(ListSerializer):
    """
    Experimental opt-in representation with ordered results and bounded tasks.

    Select a subclass with ``Meta.list_serializer_class`` and implement
    ``get_item_serializer()``. Each call must construct a fresh, unbound
    serializer, with its own context dict and any application-specific options.
    Only representation is concurrent; validation and writes stay unchanged.
    Intended for independent async I/O, not parallel ORM queries or CPU work.
    Measure the latency of other requests on the same worker before adopting
    it: a faster list can slow them down. See the concurrent serialization
    guide.
    """

    max_concurrency: int = 4

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if type(self.max_concurrency) is not int or self.max_concurrency < 1:
            raise ValueError("max_concurrency must be a positive integer.")
        require_sync_hooks(self, "get_item_serializer")

    def get_item_serializer(self, instance: Any) -> BaseSerializer:
        """
        Construct one item serializer in Django's thread-sensitive worker.

        For example: ``return ItemSerializer(instance, context=dict(self.context))``.
        Pass custom constructor options explicitly. Context values such as the
        request and async HTTP client may be shared, but mutable per-item state
        must not be. The item is represented as a standalone serializer root.
        """
        raise NotImplementedError(
            "Define get_item_serializer() to construct a fresh item serializer."
        )

    def _prepare_item(
        self, instance: Any, seen: weakref.WeakValueDictionary[int, BaseSerializer]
    ) -> BaseSerializer:
        serializer = self.get_item_serializer(instance)
        if inspect.iscoroutine(serializer):
            serializer.close()
        if not isinstance(serializer, BaseSerializer) or serializer.parent is not None:
            raise TypeError(
                "get_item_serializer() must return a fresh, unbound serializer."
            )
        if id(serializer) in seen:
            raise TypeError(
                "get_item_serializer() must not reuse a serializer instance."
            )
        seen[id(serializer)] = serializer
        # A custom get_fields() can query; classify it in the same worker as
        # construction, before the async representation dispatcher reads it.
        if isinstance(serializer, (Serializer, DRFListSerializer)):
            # Classification can use the existing class cache only before
            # fields are materialized. Custom/dynamic factories still opt out.
            has_async_representation(serializer)
            _prepare_fields(serializer)
        return serializer

    async def _represent_item(
        self, instance: Any, seen: weakref.WeakValueDictionary[int, BaseSerializer]
    ) -> Any:
        serializer = await run_sync(self._prepare_item)(instance, seen)
        # Do not use .data: ReturnDict would retain every per-item serializer
        # and its bound fields for the lifetime of the complete result list.
        return await aio.to_representation(serializer, instance)

    async def ato_representation(self, data: Any) -> list[Any]:
        items = await run_sync(_materialize)(data)
        results: list[Any] = [None] * len(items)
        remaining = iter(enumerate(items))
        pending: dict[asyncio.Task, int] = {}
        seen: weakref.WeakValueDictionary[int, BaseSerializer] = (
            weakref.WeakValueDictionary()
        )
        try:
            while True:
                for index, item in islice(
                    remaining, self.max_concurrency - len(pending)
                ):
                    task = asyncio.create_task(self._represent_item(item, seen))
                    pending[task] = index
                if not pending:
                    return results
                done, _ = await asyncio.wait(
                    pending, return_when=asyncio.FIRST_COMPLETED
                )
                # Errors are propagated unchanged. If several items complete
                # together, observe them in input order before starting more.
                for task in sorted(done, key=pending.__getitem__):
                    results[pending.pop(task)] = task.result()
        finally:
            if pending:
                await _cancel_and_wait(pending)


def _materialize(data: Any) -> list[Any]:
    # Includes custom managers and synchronous iterators, not just QuerySets.
    if isinstance(data, BaseManager):
        data = data.all()
    return list(data)


def _prepare_fields(serializer: Any) -> None:
    # An async override can call super().ato_representation(). Its field tree
    # must be ready even though override detection need not inspect fields.
    if isinstance(serializer, DRFListSerializer):
        _prepare_fields(serializer.child)
    elif isinstance(serializer, Serializer):
        for field in serializer.fields.values():
            if not field.write_only and isinstance(field, BaseSerializer):
                _prepare_fields(field)


async def _cancel_and_wait(tasks: Iterable[asyncio.Task[Any]]) -> None:
    for task in tasks:
        task.cancel()
    joining = asyncio.gather(*tasks, return_exceptions=True)
    cancelled = False
    while not joining.done():
        try:
            await asyncio.shield(joining)
        except asyncio.CancelledError:
            # A second disconnect/cancel must not abandon item finalizers.
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError
