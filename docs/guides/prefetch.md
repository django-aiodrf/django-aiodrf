# Prefetching for list representation (`aiodrf.contrib.builtin.list_prefetch`)

This optional integration adds no dependency and changes nothing unless a
serializer selects it.

When every item of a list needs data from somewhere else (another service, a
cache, a relation), fetch it for the whole list in one step and let DRF
represent the items as usual. This is what `prefetch_related` does for the ORM,
and `PrefetchListSerializer` is its counterpart for any awaited source:

```python
from aiodrf import serializers
from aiodrf.contrib.builtin.list_prefetch import PrefetchListSerializer


class InventoryListSerializer(PrefetchListSerializer):
    prefetch_related = ["warehouse"]  # Django relations, loaded first

    async def aprefetch(self, instances):
        response = await self.context["inventory"].post(
            "/stock/", json=[item.sku for item in instances]
        )
        response.raise_for_status()
        stock = response.json()
        for item in instances:
            item.available = stock[item.sku]  # as Prefetch(to_attr=...) would


class InventorySerializer(serializers.ModelSerializer):
    available = serializers.IntegerField(read_only=True)
    warehouse = serializers.StringRelatedField()

    class Meta:
        model = Item
        fields = ["sku", "available", "warehouse"]
        list_serializer_class = InventoryListSerializer
```

Execution order:

1. The data is materialized: a list or tuple as it is, a queryset or manager in
   the request's worker thread (one hop), after pagination if the view
   paginates, so only the page is fetched for.
2. `prefetch_related` lookups (names or `Prefetch` objects) are loaded with
   Django's `aprefetch_related_objects()`.
3. `aprefetch(instances)` is awaited once, with the whole list. Put what the
   items need on them.
4. The items are represented by DRF's `ListSerializer.to_representation` in one
   worker hop, in order, by the one child serializer. If the child still has
   async fields of its own, aiodrf's item walk represents them instead.

Synchronous callers (the browsable API, `serializer.data` in a thread) get the
same result through aiodrf's bridge. An exception in `aprefetch` is the
request's exception: an `APIException` becomes DRF's error response. A
cancelled request cancels the awaited step; there is nothing else to join.

## Performance and selection criteria

Batching can reduce the number of upstream requests. If the source only accepts
per-item operations, concurrent requests can shorten the list's completion time
while increasing load and other requests' tail latency. Measure both the target
endpoint and competing requests, including timeouts and upstream rate limits.
With one item there is nothing to batch and the preparation step adds overhead.

Hops (aiodrf's thread hops, `aiodrf.test.count_hops()`): in a generic list, the
action's hop evaluates the page, and one more represents the items; Django's
`aprefetch_related_objects()` runs its own thread work and is not counted.

## Compared with `aiodrf.contrib.builtin.concurrent`

`ConcurrentListSerializer` represents items in parallel tasks, each with a
fresh serializer. It needs no batching source, but bursts of task completions
can increase other requests' tail latency. Prefer prefetch preparation when the
source supports batching; see [concurrent serialization](concurrent-serialization.md)
for bounded per-item concurrency and its cancellation contract.
