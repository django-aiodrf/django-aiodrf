# Extension hooks and async boundaries

aiodrf preserves DRF's extension points; it does not make every method in DRF
awaitable. Use the pairs below for asynchronous work. Keep synchronous factories
and callbacks synchronous unless their caller explicitly supports an async pair.
Changing an arbitrary `def` to `async def` is not a safe migration.

## Supported view pairs

| Synchronous DRF hook | Async counterpart |
| --- | --- |
| `initial` | `ainitial` |
| `perform_authentication` | `aperform_authentication` |
| `check_permissions` | `acheck_permissions` |
| `check_object_permissions` | `acheck_object_permissions` |
| `check_throttles` | `acheck_throttles` |
| `handle_exception` | `ahandle_exception` |
| `finalize_response` | `afinalize_response` |
| `get_queryset` | `aget_queryset` |
| `get_object` | `aget_object` |
| `get_serializer` | `aget_serializer` |
| `get_serializer_class` | `aget_serializer_class` |
| `get_serializer_context` | `aget_serializer_context` |
| `get_authenticate_header` | `aget_authenticate_header` |
| `filter_queryset` | `afilter_queryset` |
| `paginate_queryset` | `apaginate_queryset` |
| `get_paginated_response` | `aget_paginated_response` |
| `perform_create` | `aperform_create` |
| `perform_update` | `aperform_update` |
| `perform_destroy` | `aperform_destroy` |
| `get_etag` | `aget_etag` |
| `get_last_modified` | `aget_last_modified` |
| `get_validated_query_params` | `aget_validated_query_params` |

The generic-view and persistence hooks require the corresponding generic view
or mixin. HTTP handlers and CRUD actions (`list`, `create`, `retrieve`, `update`,
`partial_update`, `destroy`) keep their ordinary names. They may be coroutines;
there is no view-level `alist` or `acreate` convention. [Async naming](../architecture/naming.md)
records why, and `aiodrf.W006` reports adrf's action names on a view.

For a supported pair, the nearest override in the MRO wins. If a class defines
both members, the async member wins. A coroutine under the original name is
also awaited by the async dispatcher. Prefer the `a...` counterpart for a new
typed override: it keeps the original DRF signature usable by synchronous
consumers. Unknown synchronous overrides run in a worker, once; they are not
executed speculatively on the event loop and retried.

Synchronous bridges support DRF's synchronous callers, such as metadata and
schema generation, from synchronous code or a worker. Do not call a synchronous
bridge to an async override directly on an already-running event loop; use
the async counterpart there. Inside an async override, await `super().a...()`.

## Serializer factories and authentication challenges

Generic views await `aget_serializer_class()` and `aget_serializer_context()`
before constructing a serializer in the worker. An explicit `context=` argument
is retained without calling the context hook. Dynamic serializer classes pass
through the same schema adaptation and backend allowlist checks as static ones.

```python
class ReportView(generics.ListAPIView):
    serializer_class = ReportSerializer
    queryset = Report.objects.all()

    async def aget_serializer_context(self):
        context = dict(await super().aget_serializer_context())
        context["currency"] = await self.request.pricing.currency()
        return context
```

The example assumes application-defined `Report`, `ReportSerializer` and an
async pricing service. Selection hooks may also be used while deriving ORM
prefetch lookups or generating metadata, not only to serialize a response.
Keep them safe to call more than once and provide schema-time defaults.
Serializer factory pairs require `GenericAPIView` or a generic viewset; adding
an async factory to a nongeneric `APIView` does not install that protocol.

Authentication classes may implement `aauthenticate_header(request)`; the view
may instead override `aget_authenticate_header(request)`. The first configured
authenticator supplies the default challenge, as in DRF. A nonempty challenge
produces `WWW-Authenticate` with a 401; no challenge produces a 403. Unknown
synchronous header hooks and authenticator constructors run in the worker.
`BaseAuthentication.authenticate_header()` bridges async overrides for ordinary
synchronous DRF callers. Cancellation is propagated, not converted to a 403.

## Conditional requests

`get_etag` and `get_last_modified` take the arguments of Django's
`condition()` callables (`request, *args, **kwargs`) and return the same
values: an ETag, quoted or not, a `datetime` (naive is read as UTC), or None.
Unlike Django's decorator, which calls them before the view and synchronously
even for an async view, aiodrf calls them after authentication, the view's permissions and
throttling, so a 304 or 412 is never an answer to a client those refuse.
Object permissions and the view's queryset scope have not been applied yet:
a validator that looks the object up must apply them itself, or a client
could learn whether an object it may not see exists, and its version. On a
generic view, `aget_object()` does (the queryset, filters, lookup and object
permissions), at the cost of reading the object once more in the handler:

```python
class ArticleDetail(generics.RetrieveAPIView):
    queryset = Article.objects.all()  # or a tenant-scoped get_queryset()
    serializer_class = ArticleSerializer

    async def aget_etag(self, request, pk):
        article = await self.aget_object()  # 404 or 403 as the retrieve would
        return f"{article.pk}-{article.version}"
```

Django's `get_conditional_response` decides (RFC 9110 section 13.2.2): a GET or
HEAD whose `If-None-Match` or `If-Modified-Since` matches gets a 304 and the
handler does not run; a failed `If-Match` or `If-Unmodified-Since`, or an
`If-None-Match` on a write, raises `aiodrf.exceptions.PreconditionFailed`, a
412 in DRF's error format (Django's decorator returns an empty 412). Safe
responses get `ETag` and `Last-Modified` unless the handler set them. Writes
without precondition headers do not call the hooks. Checking `If-Match` and
saving are two steps, as with Django's decorator: two writers can pass the
same check. Guard the write itself (a version column in the `UPDATE`'s
`WHERE`) where that matters.

## Query parameters

`query_serializer_class` (or `get_query_serializer_class()`) names a
serializer that validates `request.query_params`. Nothing validates it
automatically; a handler or `get_queryset` asks for the result:

```python
class Filters(serializers.Serializer):
    tag = serializers.ListField(child=serializers.CharField(), required=False)
    limit = serializers.IntegerField(min_value=1, max_value=100, default=20)


class Articles(generics.ListAPIView):
    query_serializer_class = Filters

    def get_queryset(self):  # runs in the worker with the rest of ``list``
        params = self.get_validated_query_params()
        queryset = Article.objects.all()
        if "tag" in params:
            queryset = queryset.filter(tags__name__in=params["tag"])
        return queryset[: params["limit"]]
```

The result is the serializer's `validated_data`, computed once per request;
invalid input raises DRF's `ValidationError` (400). The `QueryDict` is passed
as the serializer's data, so DRF's HTML-input rules apply: a `ListField`
reads every value of a repeated key (`?tag=a&tag=b`) and an absent
`BooleanField` is `False`, not missing. A declarative serializer validates on
the loop; a project's `get_query_serializer_class()`, or a serializer with
code of its own, is built and validated in one worker hop. With
drf-spectacular, `aiodrf.contrib.spectacular.AutoSchema` documents the fields
as query parameters (ecosystem guide, section 2).

## Finalizing a response

```python
from typing import Any

from django.http import HttpResponseBase

from aiodrf.request import Request
from aiodrf.views import APIView


class TaggedView(APIView):
    async def afinalize_response(
        self,
        request: Request,
        response: HttpResponseBase,
        *args: Any,
        **kwargs: Any,
    ) -> HttpResponseBase:
        response = await super().afinalize_response(request, response, *args, **kwargs)
        response["X-API-Version"] = "1"
        return response
```

An existing synchronous `finalize_response` override needs no rewrite. DRF's
negotiation, renderer context, `Vary` and response-header semantics remain in
the default finalizer. Custom synchronous `get_renderer_context()` runs in a
worker; the framework default does not add that hop. Finalization also applies
to responses returned by the exception handler. An exception in finalization
itself propagates, as in DRF; it is not recursively sent through finalization.
See [DRF's APIView contract](https://www.django-rest-framework.org/api-guide/views/).

An aiodrf `Response` whose renderer can render on the loop has a `render`
that is marked as a coroutine function (implementation guide, section 5): awaiting
it renders inline or in one worker hop. Synchronous callers that are not on
the event loop (Django's sync handler, the test client, `cache_page`) get the
rendered response back directly. Code that calls `response.render()`
synchronously *on* the event loop thread, for example an async middleware,
should check `inspect.iscoroutinefunction(response.render)` and await
it; otherwise, for data that needs the worker, it receives an awaitable.

## Policy and serializer hooks

Authentication supports `authenticate` / `aauthenticate`; permissions support
`has_permission` / `ahas_permission` and `has_object_permission` /
`ahas_object_permission`; throttles support `allow_request` / `aallow_request`.
Filter and pagination hooks have their corresponding `a...` dispatchers.
Keep policy constructors and `wait()` synchronous. Authentication challenges
also support `aauthenticate_header()`.
A throttle's `wait()` is its own decision: an `aallow_request()` does not
make it safe on the event loop, so a `wait()` the project wrote runs in a
worker unless it is declared pure.

aiodrf serializers expose `ais_valid()`, `asave()` and `adata()`. Their
validation, representation and persistence extension points are described in
the [implementation guide](implementation.md). Plain DRF serializers remain
usable through `aiodrf.aio`; they do not acquire these instance methods.
Views must use `await request.adata()` when parsing may perform I/O, and
`await request.auser()` / `await request.aauth()` for lazy authentication.

## Hooks that remain synchronous

Keep these synchronous:

- View factories and context builders: `initialize_request`, `get_parsers`,
  `get_authenticators`, `get_renderers`, `get_permissions`, `get_throttles`,
  `get_query_serializer`, `get_query_serializer_class` and query context builders.
- Content negotiation and version selection, exception
  handler selection/context and success headers.
- `permission_denied` and `throttled`, which DRF calls to raise. An
  `async def` would return a coroutine nobody awaits and let the request
  through; put asynchronous auditing in a permission's or throttle's async hook.
- Parser `parse`, renderer `render`, and metadata `determine_metadata`,
  `determine_actions`, `get_serializer_info`, `get_field_info`.

These callbacks can contain synchronous project code behind their existing
worker boundary; the metadata constructor and its computation, for example,
run together in one worker. Use the supported `aget_serializer*` counterparts
for async factory work, with schema-time defaults where needed.

`APIView.as_view()` validates known synchronous hooks and statically configured
components without constructing them. A detectable coroutine in those hooks
raises `ImproperlyConfigured` with the class and method name. It also resolves
the static serializer: a bare msgspec Struct or pydantic model is wrapped, and
a kind that `ALLOWED_SERIALIZER_BACKENDS` excludes is refused
([msgspec and pydantic guide](msgspec-pydantic.md#in-views)). The same applies
to aiodrf viewsets through their cooperative `as_view()` chain. Parsers,
renderers and metadata selected dynamically are also checked at their execution
boundary. The internal hook registry is not an application extension API.

A decorator that wraps an `async def` method with a synchronous function
(`functools.wraps`) returns a coroutine, but its own code runs before that. The
dispatcher calls such a handler, `@api_view` function, exception handler or
`a...` pair member in a worker and awaits the coroutine on the event loop; so
do `aiodrf.aio` and aiodrf's serializers for validators, `validate_<field>` /
`validate` hooks and their `a...` members, `SerializerMethodField` methods and
serializer pair members. A model
method used as a `source` is called by DRF's `get_attribute`, in the worker
under `REPRESENTATION_MODE = "thread"`. A wrapper that only creates
the coroutine can say so with `asgiref.sync.markcoroutinefunction` (or
`inspect.markcoroutinefunction`); it is then called on the event loop.

This is a diagnostic, not whole-program verification. Arbitrary descriptors,
unannotated synchronous wrappers returning awaitables, runtime monkeypatches
and nested hooks in third-party libraries cannot all be classified statically.
Registration does not prove an apparently async method is non-blocking. Keep
the library's conservative worker defaults; declare `@async_safe` only when
the project's entire synchronous call path is known not to block.
