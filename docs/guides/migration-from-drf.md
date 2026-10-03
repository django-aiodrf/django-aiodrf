# Migrating from DRF

aiodrf subclasses DRF and keeps its names, so a project moves one endpoint at
a time. The [implementation guide](implementation.md) says what happens to each
kind of code; this guide describes migration. Shared configuration and execution
boundaries are documented in [Using aiodrf with DRF](drf-integration.md).

## 1. Before moving an endpoint

Record what the endpoint does now: bodies, status codes and headers for valid
and invalid requests, authentication and permission behaviour, the schema,
the query count. Those are the acceptance criteria afterwards.

List the hooks the view and its serializers override (`initial`,
`get_queryset`, `get_permissions`, `perform_create`, `finalize_response`,
filters, fields, validators, renderers, the exception handler). Synchronous
DRF hooks retain their contracts through worker adaptation. Verify custom
composition and thread-local assumptions: running a hook in a worker thread
does not guarantee that every extension behaves as before.
The inventory also identifies hooks that could use awaited I/O.

No database change is needed. Decide the transaction boundary first if the
view relies on `ATOMIC_REQUESTS` (section 5).

## 2. Imports

```python
from rest_framework import serializers
from django_filters.rest_framework import DjangoFilterBackend
from aiodrf import viewsets


class BookSerializer(serializers.ModelSerializer):  # unchanged
    class Meta:
        model = Book
        fields = ["id", "title"]


class BookViewSet(viewsets.ModelViewSet):  # the only changed import
    queryset = Book.objects.all()
    serializer_class = BookSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["title"]
```

aiodrf's generic views drive plain DRF serializers. Filter backends,
paginators, authentication and permission classes stay the ones configured
in `REST_FRAMEWORK`; routers and action names are DRF's. Add `"aiodrf"` to
`INSTALLED_APPS` for the system checks and the drf-spectacular extensions.

`python -m aiodrf.codemod` rewrites the imports (section 6).

## 3. Settings

The defaults are the conservative choices:

```python
AIODRF = {
    "VALIDATION_UNKNOWN": "thread",  # validation code aiodrf cannot classify
    "REPRESENTATION_MODE": "thread",  # serializer.data asked for from async code
}
FASTDRF = {"SERIALIZER_BACKEND": "drf"}  # django-fastdrf, which aiodrf builds on
```

`"inline"` for the first two is the project's assertion that the code never
blocks, or that the instances are loaded; a query made there raises
`SynchronousOnlyOperation` and nothing is repeated in a thread.
`"optimistic"` is not supported: it raises `ImproperlyConfigured`, and
`manage.py check` reports it as `aiodrf.E001`. Do not set
`DJANGO_ALLOW_ASYNC_UNSAFE` to hide an error.

## 4. Async application code

```python
from aiodrf import serializers
from aiodrf.response import Response
from aiodrf.views import APIView


class MessageSerializer(serializers.Serializer):
    text = serializers.CharField(max_length=200)


class MessageView(APIView):
    async def post(self, request):
        serializer = MessageSerializer(data=await request.adata())
        await serializer.ais_valid(raise_exception=True)
        return Response(await serializer.adata())
```

- aiodrf serializers have `ais_valid()`, `asave()` and `adata()`. For a plain
  DRF serializer use `await aio.is_valid(serializer)`, `aio.save`, `aio.data`
  from `aiodrf.aio`.
- In a hand-written async handler read the body with `await request.adata()`;
  `request.data` is still DRF's synchronous property. The default
  `aperform_authentication` has loaded `request.user` before the handler
  runs. A view that overrides `perform_authentication` to authenticate lazily
  calls `await request.auser()` where it needs the user. Reading
  `request.user` on the event loop cannot run the authenticators, so aiodrf
  still authenticates before a permission or throttle that runs there and may
  read the user: DRF's rate throttles, and any class with async members.
  DRF's `AllowAny`, and synchronous policies run in a worker, stay lazy.
- Every hook is a sync/async pair: `get_queryset` / `aget_queryset`,
  `get_object` / `aget_object`, `perform_create` / `aperform_create`,
  `validate` / `avalidate`, `validate_<field>` / `avalidate_<field>`,
  `create` / `acreate`, `to_representation` / `ato_representation`.
  Implement either member. The other one bridges to it, so a synchronous
  caller (the browsable API, schema generation, a legacy `initial()`) reaches
  an async-only hook and an async caller reaches a synchronous one. Inside
  an async member, call the async member of `super()`
  (`await super().ato_representation(instance)`): the synchronous one,
  called on the event loop, raises `RuntimeError`. The
  bridges are aiodrf's serializers': a plain DRF serializer's async members
  run only where aiodrf calls them (its generic views, `aiodrf.aio`). An
  aiodrf view's `perform_create()` or `perform_update()` written for DRF
  raises `ImproperlyConfigured` for such a serializer instead of skipping
  `acreate()`; DRF's own `is_valid()`, `save()` and `.data`, called by a DRF
  view or by a handler of the project's, leave the coroutines unawaited
  (system check `aiodrf.W010` reports the DRF views). Inherit from
  `aiodrf.serializers`, or write the async member of the caller
  (`aperform_create`).
- Async policies subclass `aiodrf.permissions.BasePermission`
  (`ahas_permission`, `ahas_object_permission`),
  `aiodrf.authentication.BaseAuthentication` (`aauthenticate`) or
  `aiodrf.throttling.BaseThrottle` (`aallow_request`).
- Async validators and hooks are awaited in DRF's order, and synchronous
  work is never moved across one to save a hop: two relation fields with an
  async validator between their lookups cost two hops.

Synchronous code that does no I/O can be declared, and then runs on the
event loop instead of a thread:

```python
from rest_framework.permissions import BasePermission
from aiodrf.utils import async_safe


@async_safe
class IsOwner(BasePermission):
    def has_object_permission(self, request, view, obj):
        return obj.owner_id == request.user.pk
```

The declaration covers what the class defines. A subclass that adds methods
has to be declared itself.

## 5. Transactions

Django refuses `ATOMIC_REQUESTS` for async views (`aiodrf.W002` reports the
setting at start-up). aiodrf has `ATOMIC_SAVE` instead, on by default: the
default synchronous save, `create()` or `update()` with their many-to-many
writes, runs in `transaction.atomic()` on the database the router selects. It
does not cover `perform_create` as a whole, an `acreate`/`aupdate`, a
serializer's own `save()` override (nested-write packages such as
drf-writable-nested have one: wrap `perform_create` in `transaction.atomic()`
there) or several databases. On PostgreSQL it costs two round trips;
`AIODRF = {"ATOMIC_SAVE": False}` gives DRF's autocommit behaviour.

An operation that must be atomic across several writes belongs in one
synchronous function with `transaction.atomic()` inside it, awaited once
through `sync_to_async`. Keep the connection and the transaction in that
function: neither can move between threads. A synchronous operation that is
already running finishes even if the client disconnects; do not retry writes
automatically.

## 6. The codemod

```console
python -m aiodrf.codemod --diff myproject/api/    # show what would change
python -m aiodrf.codemod --check myproject/api/   # exit 1 if anything would
python -m aiodrf.codemod myproject/api/           # rewrite the files
```

`from rest_framework.<module> import <names>` moves each name aiodrf provides
to `aiodrf.<module>` and leaves the rest on DRF; `from rest_framework import
<module>` moves when every attribute the file uses exists in aiodrf, and is
kept with a note on stderr otherwise. A directory scan skips environments,
vendored and built code and `migrations` (the list is in the
[adrf guide](migration-from-adrf.md#2-the-codemod)); a file named on the
command line is always converted, keeping its encoding, line endings and
permissions. A file it cannot parse is named on stderr (`not parsed`) and
left as it is; the others are still converted, and the run exits with
status 2.
It changes imports, not behaviour: blocking calls inside `async def` code and
transaction boundaries are the project's to review. A second run produces no
diff.

## 7. Caching, schema, serializer backends

- `django.views.decorators.cache.cache_page` on an **async** handler looks
  the response up synchronously on the event loop. Use
  `aiodrf.cache.cache_page` there; on a synchronous handler Django's is fine.
  See the [ecosystem guide](ecosystem.md#caches).
- The schema is drf-spectacular's as before. Compare the generated schema
  before and after the move.
- `SERIALIZER_BACKEND = "msgspec"` or `"pydantic"` is an optimization for
  list endpoints, enabled per serializer first. See the
  [serializer guide](msgspec-pydantic.md).

## 8. Rollout

Move one endpoint, run its recorded requests against it, and compare: status,
headers, bodies and error codes, the schema, the query count. `count_hops()`
from `aiodrf.test` shows where a request leaves the event loop:

```python
with count_hops() as hops:
    response = await AsyncAPIClient().get("/books/")
assert hops.calls == ["ListModelMixin._list"]
```

Keep the DRF view importable until the endpoint has run in production;
switching back is one import.

A sample DRF application converted with the codemod behaves as before with
mixed base classes, serializer context, django-filter, denied requests,
exceptions in custom actions and the generated schema. Check your own custom
hooks after converting.

Generic model serializers need no runtime patch to Django or DRF:

```python
from aiodrf import serializers


class BookSerializer(serializers.ModelSerializer[Book]):
    class Meta:
        model = Book
        fields = ["id", "title"]
```

The package ships type information (`py.typed`), including for the optional
contrib modules, so type checkers verify code like this.
