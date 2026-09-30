"""
Batch enrichment before list representation (opt-in).

``PrefetchListSerializer`` is aiodrf's ``ListSerializer`` with one awaited step
before its items are represented, as ``prefetch_related`` is for the ORM: the
list fetches for all of its items at once and puts the results on the
instances; then DRF represents the items as usual, and their fields read what
was fetched like any other attribute::

    class InventoryListSerializer(PrefetchListSerializer):
        prefetch_related = ["warehouse"]  # Django relations, as for a QuerySet

        async def aprefetch(self, instances):
            response = await self.context["inventory"].post(
                "/stock/", json=[item.sku for item in instances]
            )
            response.raise_for_status()
            stock = response.json()
            for item in instances:
                item.available = stock[item.sku]


    class InventorySerializer(serializers.ModelSerializer):
        available = serializers.IntegerField(read_only=True)

        class Meta:
            model = Item
            fields = ["sku", "available"]
            list_serializer_class = InventoryListSerializer

One awaited call per list instead of one per item: the item fields stay
synchronous and all items are represented by DRF's ``ListSerializer`` in one
worker hop, in order, by the same child serializer. Items that still have async
fields of their own are represented by aiodrf's item walk, as without this class. See ``docs/guides/prefetch.md``.
"""

from collections.abc import Sequence
from typing import Any

from django.db.models import Prefetch
from django.db.models.query import aprefetch_related_objects
from rest_framework import serializers

from aiodrf import aio
from aiodrf.aio._classify import has_async_representation
from aiodrf.aio._represent import _alist
from aiodrf.serializers import ListSerializer
from aiodrf.utils import run_sync

__all__ = ["PrefetchListSerializer"]


class PrefetchListSerializer(ListSerializer):
    """``ListSerializer`` with ``aprefetch()`` before the items are represented."""

    #: Relations of the instances to load, as ``QuerySet.prefetch_related``
    #: takes them (names or ``Prefetch`` objects), with Django's
    #: ``aprefetch_related_objects()``.
    prefetch_related: Sequence[str | Prefetch] = ()

    async def aprefetch(self, instances: list[Any]) -> None:
        """
        Load onto ``instances`` what their representation reads, for all of
        them at once. Called with the materialized list, after
        ``prefetch_related``; the default does nothing.
        """

    async def ato_representation(self, data: Any) -> list[Any]:
        # Exact lists as they are; anything else, a manager's all() included,
        # is evaluated in the worker.
        instances = await _alist(data)
        if self.prefetch_related:
            await aprefetch_related_objects(instances, *self.prefetch_related)
        await self.aprefetch(instances)
        represented = await run_sync(self._represent)(instances)
        if represented is aio.NEEDS_AWAIT:
            # The items have async fields of their own: aiodrf's item walk.
            represented = await super().ato_representation(instances)
        return represented

    def _represent(self, instances: list[Any]) -> Any:
        # In the worker: building the child's fields may run project code.
        if has_async_representation(self.child):
            return aio.NEEDS_AWAIT
        return serializers.ListSerializer.to_representation(self, instances)
