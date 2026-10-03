# Native async ORM (`aiodrf.contrib.async_backend`)

This opt-in integration is tested with django-async-backend 6.1.5, Django 6.1
and DRF 3.18 on PostgreSQL. It supports a different set of features than
Django's standard ORM, described below.

[django-async-backend](https://pypi.org/project/django-async-backend/) gives
each model a second manager, `Model.async_objects`, whose queries run on an
async psycopg connection. With this contrib, aiodrf's generic views read,
write and delete through that manager in the request's task. What DRF does
without the database stays DRF's: authentication, permissions, validation and
representation, in aiodrf's worker thread as usual.

## Configuration

```console
pip install "django-aiodrf[async-backend]" "psycopg[binary]"
```

```python
INSTALLED_APPS = [
    ...,
    "django_async_backend",
]

DATABASES = {
    "default": {
        # Subclasses Django's PostgreSQL engine: the synchronous ORM keeps working.
        "ENGINE": "django_async_backend.db.backends.postgresql",
        ...
    }
}
```

The package's app is required. When Django loads it, it adds the async
members that native writes use (`async_objects`, `async_save`,
`async_delete`, ...) to `django.db.models.Model`, and it closes the async
connections at the end of each request. That patch is the package's, and
installing the app is the project's choice; aiodrf patches nothing. Without
the app, `as_view()` raises `ImproperlyConfigured`.

## Views, serializers and paginators

```python
from aiodrf.contrib import async_backend as native
from aiodrf.contrib.async_backend.filters import DjangoFilterBackend
from aiodrf.contrib.async_backend.pagination import PageNumberPagination


class BookSerializer(native.ModelSerializer):
    class Meta:
        model = Book
        fields = ["id", "title", "author", "tags"]
        auto_prefetch = True


class BookViewSet(native.ModelViewSet):
    queryset = Book.async_objects.order_by("id")
    serializer_class = BookSerializer
    pagination_class = PageNumberPagination
    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]
    filterset_fields = ["author"]
    search_fields = ["title"]
```

| aiodrf / DRF | Native counterpart | Native execution scope |
| --- | --- | --- |
| generic views and viewsets (`ListAPIView`, ..., `ModelViewSet`) | same names in `aiodrf.contrib.async_backend` | list rows, `aget_object` (`aget`), `perform_destroy` (`async_delete`, cascades included) |
| `ModelSerializer` | `aiodrf.contrib.async_backend.ModelSerializer` | `create` (`async_objects.acreate`) and `update` (`async_save`), to-many fields set as Django's `set()` does |
| `PageNumberPagination`, `LimitOffsetPagination`, `CursorPagination` | same names in `.pagination` | the count (`acount`) and the page's rows |
| django-filter's `DjangoFilterBackend`, `FilterSet` | same names in `.filters` | nothing: django-filter's `FilterSet` refuses querysets that are not Django's `QuerySet` class; this one does not check |
| `GenericAPIView` mixed with your own base | `NativeViewMixin`, placed before the aiodrf view | as above |

The views refuse, when the URL is built, a paginator that counts and slices
the queryset synchronously; at the first request, a queryset that is not a
native one.

`ModelSerializer.create`/`update` and the paginators' `paginate_queryset`
follow DRF's methods line by line, and `aset_many` follows Django's
`ManyRelatedManager.set()`. A DRF, Django or django-filter release that
changes one of these methods is detected by aiodrf's tests before it is
supported.

### Relations

- `select_related` lookups (the view's queryset, `Meta.auto_prefetch`) are
  applied to the native queryset.
- The native queryset has no `prefetch_related`. The prefetch lookups of
  `Meta.auto_prefetch` and of the view's `prefetch_related` attribute are
  loaded on the rows after they are read, with Django's
  `aprefetch_related_objects`. That runs on Django's connection.
- Relations read lazily during the representation go through Django's
  connection in the worker thread, as they do without this contrib.
- To-many fields are written through their auto-created through model's
  `async_objects`, with the same `m2m_changed` signals as Django, symmetrical
  relations included. When the related model's default manager filters (a
  soft-delete manager, for example), the ids already set are read through
  it on Django's connection, as Django does, so rows it hides stay.
- The alias of each write aiodrf makes (create, update, to-many set,
  destroy) is asked of the project's `DATABASE_ROUTERS` in a worker and
  passed to django-async-backend, which would otherwise ask them on the event
  loop. Native reads without `.using()` still ask `db_for_read()` there, and
  Django asks `db_for_write()` when a model instance is built with a related
  instance (`Book(author=author)`): routers that query need `.using()` or
  must not block.
- `aset_many()` evaluates a Django queryset it is given in a worker, and a
  native one natively.

## Transactions and signals

With `AIODRF["ATOMIC_SAVE"]` (the default), a create or update runs in
django-async-backend's `async_atomic`, together with its to-many writes.
A failure anywhere inside rolls both back. Setting to-many fields and a
delete that sends signals run in a native transaction of their own.

Django sends the write's signals (`pre_save`, `post_save`, `pre_delete`,
`post_delete`, `m2m_changed`) inside that native transaction, and runs
synchronous receivers in a thread, on Django's connection. When any receiver
is connected to these signals, aiodrf opens Django's `transaction.atomic()`
in that thread before the native transaction and ends it after it:

- `transaction.on_commit()` callbacks registered by synchronous receivers
  run after the native commit, in that thread (off the event loop), and are
  dropped when the native transaction rolls back. django-cleanup deletes the
  replaced or deleted file only once the change is committed.
- django-cacheops invalidates after the native commit (it defers inside
  Django's `Atomic`), so a concurrent read cannot cache the old row again.
- The receivers' own queries run in Django's transaction, committed right
  after the native one or rolled back with it. The two are separate
  connections: a receiver that writes a row the native transaction has
  written can wait for that transaction while the transaction waits for the
  receiver. Avoid cross-connection writes to the same locked rows. Receiver
  writes retain their locks until their own transaction completes.
- A request cancelled while that thread enters or ends Django's
  transaction (a client disconnect, a timeout) waits for it: a transaction
  it entered is exited there once, rolled back, and a commit already under
  way completes; then the cancellation goes on. Django's connection is
  never left inside the transaction.

This covers aiodrf's own writes: `acreate`, `aupdate` and `aset_many` of
the contrib's `ModelSerializer` and the views' `aperform_destroy` /
`perform_destroy`. A write inside the project's own `async_atomic` block is
not covered: aiodrf leaves Django's connection alone there, since the native
commit comes later, when the project's block ends, and receivers' callbacks
run at once, as they do for any native write outside aiodrf.
A callback registered with the native connection's own `on_commit()` runs
after the native commit on the event loop.

What is not in the native transaction:

- validation queries (unique validators, `PrimaryKeyRelatedField` lookups),
  which run before it on Django's connection, as in DRF;
- synchronous signal receivers' queries, in Django's transaction as above;
- the prefetch after a read.

Query caches:

- django-cacheops never caches native reads: `async_objects` querysets are
  django-async-backend's, which cacheops does not patch.
- django-cachalot disables itself for a database whose engine it does not
  support, which includes django-async-backend's (a system check warns). Forced
  with `CACHALOT_USE_UNSUPPORTED_DATABASE`, it would serve stale results:
  its invalidation hooks Django's SQL compilers, which native writes do not
  use.

## Hops

A hop is one switch to a worker thread (`aiodrf.utils.run_sync`), counted by
`aiodrf.test.count_hops()`. Requests cost these hops:

| Request | Hops | Where |
| --- | --- | --- |
| list or retrieve, rows represented on the loop (below) | 0 | the read is native, the representation proven not to query |
| list, otherwise (prefetch lookups, method fields, the project's serializer factory) | 1 | the representation |
| retrieve with django-filter, `SearchFilter`, `OrderingFilter` and a synchronous object permission | 3 | the filter backends (one hop for all of them), the permission, the representation |
| create, the saved row represented on the loop (below) | 1 | validation; the write is native |
| create otherwise, or update | 2 | validation, then the representation; the write between them is native |
| create, update or delete, with model signal receivers connected | +2 | entering Django's transaction before the write and ending it after (see above) |

Rows a native read or write has loaded are represented on the event loop, as
the rows of the ordinary ORM are in thread mode
([implementation](implementation.md)), when building the serializer runs no
code of the project's (the view's `get_serializer`, `get_serializer_class`
and `get_serializer_context`, and for a create its `perform_create` and
`aperform_create`, are the framework's; the serializer class is declarative)
and the serializer reads only what the rows have loaded: columns, relations
`select_related` cached, lists prefetched. Anything else keeps the hop.

A synchronous `get_queryset()` override adds one hop before the read, since
it may query; an `async def aget_queryset()` adds none. The project's other
synchronous hooks never run on the event loop: `optimize_queryset`,
`filter_queryset` and the serializer factory (with the serializer built for
`Meta.auto_prefetch`) share the filter backends' hop, the list's serializer
is built and represents in the representation hop, and a paginator's
overridden `get_page_size`, `get_page_number`, `get_limit`, `get_offset`,
`get_ordering` or `decode_cursor` runs in a hop of its own.

A list's prefetch lookups load in the representation hop; a single
object's are loaded with Django's `aprefetch_related_objects()` before its
permissions are checked.

## Execution overhead

A native driver avoids worker-blocking row reads, but does not remove the
rest of Django's lifecycle. The package installs an async `request_finished`
receiver; Django sends that signal synchronously from response closure, which
can introduce another adapter. Evaluate connection budgets, database latency
and complete request behavior before selecting the integration.

Measured on one process against a local PostgreSQL with pooled connections
(CPU per request, 16 concurrent requests, ten-row list / one row / create),
on the machine described in
[performance](performance.md#environment-of-the-published-measurements);
other hardware gives other times:

| | Ordinary ORM | Native |
| --- | ---: | ---: |
| with the test project's synchronous middleware | 1048 / 1110 / 1211 µs | 1126 / 1124 / 1202 µs |
| without middleware | 791 / 771 / 855 µs | 809 / 800 / 845 µs |

The native path saves the thread hop of the read, about 45 µs, and spends
about as much on the copied ORM's asynchronous row iteration and on the view's
per-request decisions: at a local database it is not faster per request. What
it changes is where a request waits: no thread per query, and a cancelled
request stops its query. Django's own synchronous middleware (sessions,
CSRF, authentication, `CommonMiddleware`) adds a hop per phase to both paths.

Install `psycopg[binary]` (or `psycopg[c]`): without it psycopg parses rows,
adapts values and waits for the socket in Python. On ten small rows that is
2–5 % of the request; it grows with the rows returned. Connection setup
dominates without a pool: about 3.4 ms of CPU per request, against 1.1 ms
with `OPTIONS["pool"]`.

## Connections

- A native connection belongs to the first task that used it. Under ASGI
  that is the request's task. Two tasks of one request (`asyncio.gather`
  over native queries) cannot share it; wrap such work in the package's
  `async_new_connection()`.
- Under WSGI, Django runs each request's async view in an event loop of its
  own, and asgiref carries the request's context back to the thread. The
  views give each WSGI request a connection scope of its own
  (`async_new_connection()`), closed when the view returns.
  `test_requests_leave_no_session_open` checks that no PostgreSQL session
  is left behind.
- Synchronous callers (the browsable API, a sync hook calling
  `serializer.save()` or `view.get_object()`) run the native work on a
  connection of their own, through `async_to_sync`. The browsable API and
  DRF's `DjangoModelPermissions` (which reads `view.get_queryset().model`)
  work with native views on both handlers
  (`test_the_browsable_api_renders_native_views`,
  `test_django_model_permissions_with_a_native_view`).
- Without `keepalive`, a streaming response's source runs in the request's
  task and reads on its connection. With `keepalive`, `EventStreamResponse`
  runs the source in a task of its own, from start to end, so the request's
  connection is not its own. Give the source a connection of its own:

  ```python
  async def events():
      async with async_new_connection():
          async for book in Book.async_objects.order_by("id"):
              yield {"title": book.title}


  return EventStreamResponse(events(), keepalive=15)
  ```

  Without it, the first native query in the source raises "An async
  connection can only be used by the task that first used it" once the view
  has read natively. `test_an_event_stream_reads_natively` runs both
  through Django's ASGI handler. Django's test client cannot close
  such a stream: it sends `request_finished` synchronously on the event
  loop, and the package's receiver is async.
- Under WSGI, Django collects an async response body in an event loop of
  its own after the view has returned (and warns that it does). The views
  give that body a connection scope of its own too, closed when it ends
  (`test_an_event_stream_reads_natively_under_wsgi`, through Django's WSGI
  handler).
- A request that reads natively and loads relations or validates through
  Django uses two connections, one from each engine. Both engines read the
  same `DATABASES` entry, so `OPTIONS["pool"]` gives each its own pool:
  the database sees the sum.

## Limits

- PostgreSQL only, Django 6.1 only. The package copies Django 6.1's ORM, so
  the extra pins its 6.1 line. On Django 6.2 its import of
  `RemovedInDjango70Warning` warns; the contrib imports it with that one
  warning silenced.
- The native queryset has no `only()`, `defer()` or `prefetch_related()`.
- An unpaginated list reads every row, as DRF's does.
- Writes follow DRF's `ModelSerializer`: no nested writes, and to-many
  fields only with auto-created through models.
- A native driver is not inherently faster at a fixed database connection
  budget. Compare identical queries, pool limits, network latency and request
  concurrency before adopting this backend. See the [performance guide](performance.md).
