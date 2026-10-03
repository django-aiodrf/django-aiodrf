# API reference

Install the `django-aiodrf` distribution; import from `aiodrf`. Public classes
retain DRF's arguments unless a difference is described below. DRF fields,
routers, renderers, parsers and exceptions remain usable. Start with the
[DRF integration guide](guides/drf-integration.md) for a complete application.

## Views

### `aiodrf.views.APIView`

Define HTTP handlers with `async def get(self, request, *args, **kwargs)` and
the corresponding method names. `as_view()` produces Django's callable;
authentication, permission checks, throttles, negotiation and exception handling
precede/follow the handler in DRF order. Synchronous application hooks are
adapted rather than assumed nonblocking.

| Hook | Purpose |
| --- | --- |
| `ainitial(request, *args, **kwargs)` | Override the awaited initial lifecycle |
| `aperform_authentication(request)` | Authenticate before permission checks |
| `acheck_permissions(request)` | Evaluate view permissions |
| `acheck_object_permissions(request, obj)` | Evaluate object permissions |
| `acheck_throttles(request)` | Apply configured request throttles |
| `aget_authenticate_header(request)` | Select the authentication challenge |
| `ahandle_exception(exc)` | Await application exception handling |
| `aget_etag(request, *args, **kwargs)` | Supply a conditional-request validator |
| `aget_last_modified(request, *args, **kwargs)` | Supply last-modification metadata |

Use `super()` when extending lifecycle behavior. Do not replace permission
checks merely to customize query loading. See [extension hooks](guides/extension-hooks.md)
for override precedence and [HTTP QUERY](guides/http-query.md) for body queries.

### `aiodrf.decorators.api_view(http_method_names=None)`

The function-view equivalent accepts asynchronous handlers and DRF's policy
decorators. Place policy decorators below `@api_view`, as with DRF.
`aiodrf.decorators` also exports DRF's decorators (`action`,
`authentication_classes`, `permission_classes`, ..., and on DRF 3.17+
`versioning_class`, `metadata_class`, `content_negotiation_class`).

## Generic views and viewsets

`aiodrf.generics.GenericAPIView` accepts `queryset`, `serializer_class`,
`permission_classes`, `filter_backends` and `pagination_class` as in DRF.

| Awaitable operation | Synchronous counterpart |
| --- | --- |
| `aget_queryset()` | `get_queryset()` |
| `aget_object()` | `get_object()` |
| `aget_serializer(*args, **kwargs)` | `get_serializer(*args, **kwargs)` |
| `aget_serializer_class()` | `get_serializer_class()` |
| `aget_serializer_context()` | `get_serializer_context()` |
| `afilter_queryset(queryset)` | `filter_queryset(queryset)` |
| `apaginate_queryset(queryset)` | `paginate_queryset(queryset)` |
| `aget_paginated_response(data)` | `get_paginated_response(data)` |
| `aperform_create(serializer)` | `perform_create(serializer)` |
| `aperform_update(serializer)` | `perform_update(serializer)` |
| `aperform_destroy(instance)` | `perform_destroy(instance)` |

Concrete generic views are `CreateAPIView`, `ListAPIView`, `RetrieveAPIView`,
`UpdateAPIView`, `DestroyAPIView`, `ListCreateAPIView`, `RetrieveUpdateAPIView`,
`RetrieveDestroyAPIView` and `RetrieveUpdateDestroyAPIView`.

`aiodrf.viewsets.ViewSet`, `GenericViewSet`, `ModelViewSet` and
`ReadOnlyModelViewSet` use DRF routers and `rest_framework.decorators.action`.
Actions retain names such as `list` and `create`; there is no `alist` action
convention. Generic model views and serializers support a model type parameter.

`Meta.auto_prefetch=True` enables optional inferred relation loading.
`Meta.prefetch` supplies explicit relation names or top-level `Prefetch` objects.
Dynamic field declarations are evaluated per request. Explicit queryset loading
is preferable when the serializer reads relations through custom code.
See [fetch modes](guides/fetch-modes.md) and [selective optimization](guides/serializer-optimization.md).

## Serializers

### `aiodrf.serializers`

`BaseSerializer`, `Serializer`, `ListSerializer`, `ModelSerializer` and
`HyperlinkedModelSerializer` retain DRF field declarations, `Meta`, context,
binding, errors and synchronous entry points.

| Operation | Result |
| --- | --- |
| `await serializer.ais_valid(raise_exception=False)` | Boolean; populates `validated_data` and `errors` |
| `await serializer.adata()` | Representation; `await serializer.adata` is also supported |
| `await serializer.asave(**kwargs)` | Saved instance; validation is required first |
| `await serializer.acreate(validated_data)` | Override async creation |
| `await serializer.aupdate(instance, validated_data)` | Override async update |
| `await serializer.ato_representation(instance)` | Override async output conversion |

Async validators, `avalidate_<field>`, `avalidate`, asynchronous
`SerializerMethodField` methods and supported awaited sources are executed in
the documented DRF order. Synchronous `.data` does not implicitly select the
optional compiled backend. See [serializer backends](guides/msgspec-pydantic.md).

### `aiodrf.aio`

Use `await aio.is_valid(serializer, raise_exception=True)`,
`await aio.data(serializer)` and `await aio.save(serializer, **kwargs)` with
ordinary DRF serializer instances. These entry points respect application
overrides and select the appropriate execution boundary. Low-level `try_*`
functions are execution-engine APIs, not the usual application interface.

## Authentication, permissions and throttling

`aiodrf.authentication.BaseAuthentication` supports `aauthenticate(request)`
and `aauthenticate_header(request)`. The latter supplies a challenge, not an
authentication decision. `SessionAuthentication` retains Django session and
CSRF behavior. Existing DRF authenticators remain supported.

`aiodrf.permissions.BasePermission` supports `ahas_permission(request, view)`
and `ahas_object_permission(request, view, obj)`. DRF `&`, `|` and `~`
composition retains short-circuit and object-permission semantics.

`aiodrf.throttling.BaseThrottle` supports `aallow_request(request, view)`.
Keep `wait()` synchronous; aiodrf adapts a custom blocking implementation in
the worker. The optional `FixedWindowRateThrottle`,
`AnonFixedWindowRateThrottle`, `UserFixedWindowRateThrottle` and
`ScopedFixedWindowRateThrottle` use cache counters. Rate limits depend on the
selected cache's atomicity; an in-process cache is not a distributed quota.

## Requests, responses and caching

`aiodrf.request.Request` retains DRF request properties. Await `request.auser()`
when lazy user resolution is possible, and `request.aauth()` for the authentication
value. Use `await request.adata()` to parse the body in a handwritten async handler;
the ordinary `request.data` property remains synchronous and a custom parser can
perform blocking I/O. View lifecycle preparation does not make arbitrary direct
property access safe on the event loop.

`aiodrf.response.Response` accepts DRF response arguments and supports Django's
sync and async rendering paths. Known in-memory payloads may render inline;
unknown callbacks and lazy data retain worker execution.

`fastdrf.response.DataResponse(data=None, status=None, headers=None,
content_type=None)` is an opt-in alternative: Django's `HttpResponse` whose
content the view renders from `data` when it finalizes it. With DRF's
`JSONRenderer` or `MsgspecJSONRenderer` accepted, status, content and headers
are those of DRF's `Response`; there is no `render()` step (no
`process_template_response` middleware) and, of DRF's attributes, only
`renderer_context`. Once rendered it keeps its content only, as Django's
responses do: `data` is None, so the payload is freed before the response is
sent (tests read `response.json()`). With any other renderer (the browsable API reads DRF's
response), or a view that defines `finalize_response` (which may change the
data after DRF's), the view answers with DRF's `Response`, carrying the
headers and cookies set on the `DataResponse`. Exceptions still produce DRF's `Response`.

| Response | Encoding |
| --- | --- |
| `StreamingResponse` | Newline-delimited JSON |
| `StreamingArrayResponse` | One streamed JSON array |
| `EventStreamResponse` | Server-sent events |
| `ServerSentEvent` | Event data and optional event/id/retry fields |

Streaming responses own their producer and close it on completion or
cancellation. See [streaming schemas](guides/streaming-schema.md) and
[proxy configuration](guides/stream-proxies.md).

`aiodrf.cache.cache_page(timeout, *, cache=None, key_prefix=None)` adapts page
caching to async views. Authentication, cookies, `Vary` and query-body identity
remain relevant; do not share private responses across principals.

## Application lifecycle and tests

`aiodrf.asgi.get_asgi_application(lifespan=...)` wraps Django's ASGI application
with an optional asynchronous context manager. `DJANGO_LIFESPAN` may name
its dotted path. `get_lifespan_state(request, ExpectedType)` accesses typed
worker-local state. See [lifespan](guides/lifespan.md).

`aiodrf.test.AsyncAPIClient` and `AsyncAPIRequestFactory` retain DRF-style
request encoding over Django's async clients. `count_hops()` observes aiodrf's
own adapters. `async with aiodrf_asgi_lifespan.testing.lifespan() as state:` runs the lifespan
around a test, and `AsyncAPIClient(lifespan=state)` gives every request a copy
of its state. Use full ASGI tests for disconnect and streaming ownership;
client tests alone do not exercise a deployed server. See [application testing](guides/testing.md).

## Optional integrations

| Namespace | Contract / guide |
| --- | --- |
| `fastdrf.prefetch` | `auto_prefetch`, `related_lookups`, `forget_lookups`; inferred query loading (django-fastdrf's) |
| `contrib.builtin.list_prefetch` | `PrefetchListSerializer.aprefetch(instances)`; [batch enrichment](guides/prefetch.md) |
| `contrib.builtin.concurrent` | [Bounded concurrent representation](guides/concurrent-serialization.md) |
| `contrib.list_serializers` | `ListSerializer`, `SchemaListSerializer` with weakly bound children; [memory](guides/performance.md#memory-and-garbage-collection) |
| `contrib.monkeypatches` | `apply`, `revert`, `applied`; opt-in DRF patches named in `AIODRF["MONKEYPATCHES"]` |
| `contrib.msgspec`, `contrib.pydantic` | Explicit schemas on aiodrf's async serializers; [backend contracts](guides/msgspec-pydantic.md) |
| django-fastdrf | The compiled backends (`msgspec`, `pydantic`, `"python"`), input recognition, field caching and copying, related lookups and the converter, configured with `FASTDRF`; [backend contracts](guides/msgspec-pydantic.md) |
| `contrib.spectacular` | Schema extensions and QUERY exclusion hook |
| `contrib.whitenoise` | `whitenoise_middleware`; [static file limitations](guides/static-files.md) |
| Django Tasks / `tasks` extra | [Django's task API and the Django 5 backport](guides/tasks.md); no replacement task module is installed |
| `aiodrf_async_cache.django_valkey.LifespanConnectionFactory` | Optional public connection-factory subclass for a lifespan-owned native cache; [configuration](guides/async-cache.md) |
| `aiodrf_async_cache.redis.AsyncRedisCache`, `aiodrf_async_cache.valkey.AsyncValkeyCache` | Async-only Django cache backends with awaited callbacks, standalone/Sentinel/Cluster clients and explicit lifespan ownership |
| `aiodrf_async_cache.codecs` | `MsgspecCodec` (`PydanticCodec` is `fastdrf.codecs.PydanticCodec`); optional typed value formats, not page-response serializers |
| `contrib.opensearch.AsyncDocumentWriter` | `aindex`, `adelete`, `abulk`; Django document preparation with native OpenSearch HTTP and caller-owned client |
| `aiodrf_async_cache.middleware` | `cache_lifespan`, `AsyncCache` protocol, `AsyncCacheMiddleware`, `AsyncUpdateCacheMiddleware`, `AsyncFetchFromCacheMiddleware`; [native cache integration](guides/async-cache.md) |
| `contrib.async_backend` | [Opt-in native PostgreSQL adapter](guides/async-backend.md) |

Authentication, storage, telemetry and other tested packages are indexed in the
[ecosystem guide](guides/ecosystem.md) and [example inventory](../examples/ECOSYSTEM.md).
Dependency configuration and complete vendor APIs are linked in
[contrib dependencies](reference/contrib-dependencies.md).
The old `aiodrf.contrib.prefetch` and `aiodrf.contrib.concurrent` imports remain
compatibility exports; implementations live in `contrib.builtin`.

All `AIODRF` settings, defaults and accepted values are listed in the
[settings reference](reference/settings.md). Read the
[limitations](reference/limitations.md) before enabling optimized or vendor-specific paths.
