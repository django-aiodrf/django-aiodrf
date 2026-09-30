# Concurrent item representation (experimental contrib)

This integration is experimental. Select it explicitly per list and validate
its effect on other concurrent requests before deployment.

Where the data source can answer for many items at once, prefer
[`aiodrf.contrib.builtin.list_prefetch`](prefetch.md): one awaited batch operation
per list, without creating a task and serializer for each active item.

`aiodrf.contrib.builtin.concurrent.ConcurrentListSerializer` overlaps independent async
I/O during list representation. It is selected with DRF's
`Meta.list_serializer_class` and needs no dependency, setting or monkeypatch. It
does not parallelize validation, creation or updates.

Use it only when each item's representation does independent async I/O, such
as an HTTP lookup. Plain serializers and msgspec/Pydantic batch serializers are
better served by their sequential or batch paths: this one adds per-item
construction, a task and worker boundaries. Measure concurrent small requests
before enabling it: a faster list can make other requests on the worker slower.

## Explicit item factory

```python
from aiodrf import serializers
from aiodrf.contrib.builtin.concurrent import ConcurrentListSerializer


class InventoryListSerializer(ConcurrentListSerializer):
    max_concurrency = 2

    def get_item_serializer(self, instance):
        return InventorySerializer(
            instance,
            context=dict(self.context),
            partial=self.root.partial,
        )


class InventorySerializer(serializers.Serializer):
    sku = serializers.CharField()
    available = serializers.SerializerMethodField()

    class Meta:
        list_serializer_class = InventoryListSerializer

    async def get_available(self, item):
        # The application owns the client, timeout and pool, e.g. via lifespan.
        response = await self.context["inventory_client"].get(
            "/inventory/", params={"sku": item["sku"]}
        )
        response.raise_for_status()
        return response.json()["available"]


# In an async view, using already loaded/paginated items:
# serializer = InventorySerializer(items, many=True, context={
#     "request": request, "inventory_client": client,
# })
# return Response(await serializer.adata())
```

The default limit is four; set another positive integer on the subclass after
measuring the upstream service. Booleans, zero, negative values and non-integers
are rejected. The limit is per list call, not global: concurrent requests,
worker processes and nested concurrent lists multiply the upstream work. Keep an
HTTP client pool and service-level limits in the application.

`get_item_serializer(instance)` must be synchronous and return a new, unbound
DRF or aiodrf serializer for each item; the default raises
`NotImplementedError`. `child` stays the serializer for list validation, writes
and schema introspection; the representation tasks do not share it.

The explicit factory avoids three forms of shared or incorrectly reconstructed state:

- DRF field deep copying reconstructs from original constructor arguments; those
  can include the entire input list, a lazy queryset, a request or a live client.
- Shallow copying retains mutable fields, bindings and cached serializer state.
- Reconstructing only from the child class can silently drop custom constructor
  options, such as a tenant-specific field projection.

Pass the constructor options explicitly: projection, tenant, partial and other
application flags. The factory's output must match the declared child. A new
context dict keeps one item's top-level changes from reaching the others, but
its values are shared: do not deep-copy requests and async clients, and create
nested mutable scratch state per item. Class attributes and module globals are
shared by every instance.

Each representation serializer is a standalone root (`parent is None`,
`root is self`). Code that depends on the outer list's `root`, `parent` or a
custom binding must be adapted in the factory, so third-party serializers may
need changes. Returning a bound serializer or reusing a live instance in the
same call raises `TypeError`.

## Execution and failure contract

- Results keep input order even if later items finish first. A freed slot takes
  the next item at once; at most `max_concurrency` item tasks exist, not one
  task per row waiting on a semaphore.
- Each item task gets its own copy of the caller's context variables; one
  item's changes do not reach the next.
- Item construction, field preparation and synchronous source iteration run in
  Django's thread-sensitive worker. aiodrf's representation dispatcher keeps
  sync hooks there; blocking I/O in the application's own `async def` hooks is
  still the application's to avoid.
- The first failure the coordinator sees aborts the call, cancels the other item
  tasks and awaits their finalizers. Failures of tasks that complete together
  are seen in input order, which says nothing about which network operation
  failed first.
- The original exception propagates, not an `ExceptionGroup`, so DRF's
  `ValidationError` keeps its HTTP handling. An item cancellation cancels the
  list. Errors from siblings during cancellation are collected and do not
  replace the original failure. Nothing is retried and no partial list is
  cached.
- Caller cancellation, ASGI disconnect included, cancels and joins outstanding
  async work, also when repeated. Cleanup is cooperative: a task that swallows
  cancellation or never finishes its finalizer delays completion. There is no
  cleanup timeout.
- Cancellation cannot stop synchronous code already running in a thread or undo
  an external side effect. Use read-only or idempotent lookups, upstream
  timeouts and resource context managers.

The source is materialized in a worker, as for any list response, and the whole
result is returned. Memory for inputs and results is O(number of items), task
bookkeeping O(concurrency); there is no streaming or backpressure, so paginate
large querysets. The weak instance-reuse check does not keep finished item
serializers alive, and item output dictionaries do not keep serializer
instances through `ReturnDict` wrappers.

## Django/DRF compatibility boundaries

Validation order, error shape, `allow_empty`, creation and update behavior come
from the existing `aiodrf.ListSerializer`. Synchronous `.data` callers reach the
same async representation through the existing bridge; async callers should use
`await serializer.adata()` or `await aio.data(serializer)`.

Querysets and related managers are evaluated in the worker, and lazy relation
fields keep their thread boundary. There is no parallel ORM execution and no
concurrent transaction: use `select_related`/`prefetch_related` as usual. Async
iterators are not list inputs; stream an async source with the streaming
responses instead.

Tests cover DRF's `Serializer`, `ModelSerializer`, fieldless `BaseSerializer`
overrides and the typed serializer adapters. drf-spectacular documents the list
as an array; this is separate from annotations for streaming responses. A factory that
returns another serializer class needs the output schema annotated by the
application: the factory is not executed during schema generation.

Design references: DRF's
[ListSerializer extension point](https://www.django-rest-framework.org/api-guide/serializers/#listserializer),
Python's [task cancellation rules](https://docs.python.org/3/library/asyncio-task.html#task-cancellation)
and Django's [async/thread-sensitive execution guide](https://docs.djangoproject.com/en/6.0/topics/async/).
