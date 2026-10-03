# Third-party packages

This page lists the Django and DRF packages tested with aiodrf, what was tested
and what to watch for. Each package runs against aiodrf views and, where DRF
has an equivalent, is compared with the same view written for DRF, through
both synchronous and asynchronous requests. A row covers the scenarios it
names, not every feature of the package. The tested versions are listed in the
[version reference](../reference/ecosystem-versions.md); setup instructions and
links to each package's documentation are in the
[optional dependency reference](../reference/contrib-dependencies.md).

In the table, a *hop* is one switch from the event loop to a worker thread to
run synchronous code; "one hop" means the whole action ran in a single switch.

## Tested packages

The native async cache backends are tested separately with django-valkey 0.4.1
and valkey-py 6.1.1, and with redis-py's `redis.asyncio` client, including
compatibility with the formats of Django's Redis backend and django-redis.
These tests cover connection pool ownership, ASGI state, errors, batches,
expiry, concurrent counters, the page-cache middleware, cancellation and
shutdown, plus Sentinel discovery, a controlled failover and cross-slot Cluster
operations. They do not cover every high-availability topology or plugin. See
[native async cache](async-cache.md) and [NoSQL databases](async-nosql.md),
which also explain the difference between Django's awaitable ORM and native
async drivers.

| Package | Tested behaviour |
| --- | --- |
| drf-spectacular | schema parity, aliases, name collisions, recursive graphs, PATCH, live payload validation and explicit SSE/NDJSON item schemas |
| django-filter | filters, a `FilterSet.filter_queryset` that queries |
| rest-filters | typed query validation, relation validation, unknown parameters, authorized queryset scope, request isolation and spectacular schema parity |
| django-async-backend | Version 6.1.5. `aiodrf.contrib.async_backend` on PostgreSQL, under ASGI and WSGI: pages, filters, retrieve, create and update with to-many fields and their signals, cascading delete, rollback, sync callers, sessions left open, hops ([guide](async-backend.md)) |
| djangorestframework-simplejwt | token from simplejwt's view authenticates; invalid, expired, foreign and missing tokens; hops |
| django-rest-knox | token authentication, revocation, malformed headers; hops |
| django-oauth-toolkit | bearer tokens, expired tokens, `TokenHasScope` and its `required_scopes` message |
| drf-auth-kit | `JWTCookieAuthentication` and `TokenCookieAuthentication` with `aiodrf.contrib.auth_kit`: token in the header, in the cookie, in both, cookie profiles by `Origin`, malformed, invalid and foreign tokens answer as DRF; no hop without a token, one with |
| dj-rest-auth | the JWT cookie of its login view authenticates aiodrf views |
| django-guardian, djangorestframework-guardian | `ObjectPermissionsFilter`, `DjangoObjectPermissions` with the `view` permission; the same answers with `aiodrf.contrib.permissions`, which asks guardian through `has_perms` |
| drf-nested-routers | nested list, retrieve, create; a child under the wrong parent is 404 |
| drf-standardized-errors | 400, 401, 404, 405 and unexpected errors as `EXCEPTION_HANDLER` |
| django-cors-headers | response headers and preflight requests; QUERY needs `CORS_ALLOW_METHODS` |
| django-simple-history | `history_user` of create, update, delete made in aiodrf's worker thread |
| django-axes | lockout after failed `BasicAuthentication` logins |
| drf-orjson-renderer | renderer and parser produce DRF's bytes |
| drf-excel | the workbook equals the one of the DRF view |
| django-cachalot | lists and retrieves served from the cache (proved with a write cachalot cannot see) and invalidated by writes from worker threads; a save rolled back after an in-transaction read leaves the cache correct; hops unchanged |
| django-cacheops | list, retrieve and Django's `aget`/`acount` in an `async def` handler served from Redis; create, update and delete invalidate; a save rolled back after an in-transaction read leaves neither the read nor the invalidation behind; hops unchanged |
| django-cleanup | update deletes the replaced file, destroy deletes the file, both after the commit and off the event loop; a rolled-back update keeps the old file (the new upload stays, as with DRF); hops unchanged |
| django-elasticsearch-dsl | `_bulk` indexing from the worker thread on create, update and delete; a search in `get_queryset` runs in the hop; native `elasticsearch.dsl.AsyncSearch`; the synchronous client in an async handler blocks the loop; Elasticsearch-only, not an OpenSearch adapter |
| django-opensearch-dsl, opensearch-py | Public document hooks in workers, native index/delete/bulk/search, deferred fields, errors and cancellation; optional live 2.x/3.x mapping/aggregation/pagination |
| django-typer | a `TyperCommand` whose handler is `async def`, run by `aiodrf.management.AsyncCommand` ([guide](management-commands.md)); without it, django-typer returns the coroutine unawaited |
| django-redis, redis-py | throttling and `cache_page`, as for every cache backend |
| django-zeal | sees the queries of aiodrf's worker thread; `Meta.auto_prefetch` leaves none to report |
| asgi-lifespan, HTTPX | real ASGI root with typed lifespan state via `manager.app`, HTTP dispatch and cleanup |
| channels, djangochannelsrestframework (+ daphne for `channels.testing`) | an aiodrf permission with only `ahas_permission`, alone and in `IsAuthenticated \| ...`, refuses or allows a WebSocket connection and its actions; actions awaiting aiodrf serializers (`ais_valid`/`asave`/`adata`, `aiodrf.aio` for DRF serializers, an async validator) answer create, patch, list and validation errors exactly as `database_sync_to_async` actions do; hops per action |
| django-health-check | database and cache checks through `aiodrf.asgi.get_asgi_application()` next to aiodrf views; a failing check is a 500 |
| django-idempotency-key | `IdempotencyKeyMiddleware`: a repeated key replays the first response (409) without a second write; a missing key is a 400; the create still costs one hop |
| djoser | users registered through its endpoints; its token login and JWT create authenticate aiodrf views; token revocation; one hop |
| django-allauth (headless) | app `X-Session-Token` (allauth's `XSessionTokenAuthentication`) and browser session logins authenticate aiodrf views; app logout; one hop |
| rules | `AutoPermissionViewSetMixin` with model rule permissions: view, change, delete, add; its synchronous `initial()` override runs in one extra hop. The synchronous rule backend grants permissions through `aiodrf.contrib.permissions` and DRF's class; do not assume Django's async permission hook invokes a backend that implements only the synchronous hook. |
| drf-api-logger | its async-capable middleware logs the same body, response and status for DRF and aiodrf views, masking included; under ASGI its signal runs on the event loop |
| apitally | its sync-only middleware counts aiodrf views like DRF views; DRF's endpoint introspection finds them; under ASGI Django runs the middleware in a thread, aiodrf adds no hop |
| wireup | constructor injection into class-based views: its synchronous replacement callback keeps the aiodrf view's coroutine marker, so Django awaits `dispatch()`; no hop |
| django-money | `MoneyField` amount and currency round-trip, model default currency, validation errors; a PATCH of the amount alone takes the default currency, as in DRF |
| django-phonenumber-field (phonenumberslite) | E164 storage, invalid numbers, `PHONENUMBER_DEFAULT_REGION` read per request; hops |
| django-taggit | `TaggitSerializer` create and update set the tags in the create's hop; `Meta.auto_prefetch` loads the tags like `prefetch_related`; `TagList` is rendered in a thread |
| drf-extra-fields (Pillow) | `Base64ImageField` decode and `PresentablePrimaryKeyRelatedField` representation run in the worker thread, never on the loop; errors as in DRF |
| django-pydantic-field (pydantic) | serializer `SchemaField`, `SchemaParser[T]`/`SchemaRenderer[T]` byte parity, its OpenAPI `AutoSchema`; the generic aliases cost three hops |
| drf-writable-nested | nested FK, reverse-FK and M2M create/update and 400s as in DRF; `UniqueFieldsMixin`; a child refused in the save keeps the parent, as in DRF (its own `save()` override is not wrapped by `ATOMIC_SAVE`); an atomic `perform_create` rolls back; one hop |
| drf-flex-fields | `expand`, `fields`, `omit` on list and retrieve with DRF's queries; `Meta.auto_prefetch` sees the fields before expansion; `FlexFieldsFilterBackend` does not import on DRF 3.18 (`coreapi`); use `FlexFieldsMixin` with aiodrf's `ModelViewSet`, not `FlexFieldsModelViewSet`; one hop |
| django-polymorphic, django-rest-polymorphic | `PolymorphicSerializer` list, retrieve, create, update and `resourcetype` errors per subclass; one hop |
| django-restql | `?query=` field selection, arguments as filters through its sync `dispatch`, eager loading, nested create/update; `ATOMIC_SAVE` rolls back a nested create refused late; one hop |
| djangorestframework-dataclasses | `DataclassSerializer` errors and ORM-less `save()` via `aiodrf.aio` in an APIView and in generic list/create; one hop |
| djangorestframework-jsonapi | renderer, parser, pagination, `QueryParameterValidationFilter`/`OrderingFilter`/`DjangoFilterBackend`, metadata, exception handler, `?include=`; `AutoPrefetchMixin`/`PreloadIncludesMixin` on an aiodrf `ModelViewSet` (DRF's bytes and queries, one hop) |
| djangorestframework-camel-case | key round-trip, errors and `CamelCaseMiddleWare` as in DRF; the unknown parser costs one hop and the renderer runs in Django's thread; neither when declared in `PURE_POLICIES`/`INLINE_RENDERERS` |
| nested-multipart-parser | nested multipart with a file, `request.data`/`request.FILES` as in DRF (with `DRF_NESTED_MULTIPART_PARSER={"querydict": False}`), one hop |
| drf-tweaks | `ApiVersionMixin` with `DeprecationMiddleware`, `AutoOptimizeMixin` (DRF's SQL), `@autofilter`, `NoCountsLimitOffsetPagination` (its `OrderedDict` page renders on the loop, like a `dict`); one hop |
| djangorestframework-datatables | renderer, filter backend and paginator sharing counts on the view in one hop; DRF's bytes |
| drf-restwind | Browsable API templates: list and detail pages with forms, same HTML as DRF |
| servestatic | ASGI static delivery: GET/HEAD, ETag, ranges, gzip, missing/parent paths and API forwarding; no middleware patch |
| whitenoise | Original sync middleware and opt-in dual-mode adapter; ASGI synchronous-file buffering remains explicit. HTTP file semantics, worker lookup and cancellation cleanup |
| django-silk | request, response and worker-thread queries recorded as in DRF (not INSERTs, in DRF too); its sync middleware adds one crossing each way, no aiodrf hop |
| django-debug-toolbar | toolbar in Browsable API pages, the SQL panel sees worker-thread queries, history panel, streaming untouched; profiler off under ASGI; no aiodrf hop |
| django-structlog | request id and user bound by `RequestMiddleware` in logs from worker-thread hooks and async handlers; `request_failed` for unhandled errors; concurrent requests keep their own context; no aiodrf hop |
| django-log-request-id | the request id of its synchronous middleware in logs from worker-thread hooks and async handlers, `request.id` and the response header; concurrent requests keep their own id |
| django-tenants | per-domain schema for lists, retrieve, create and the async ORM; concurrent requests to different tenants through Django's ASGI handler on PostgreSQL; requests sharing one thread (Django's in-process async test client) share the schema, as for any Django async view |
| django-mongodb-backend, django-mongodb-extensions (PyMongo, MongoDB) | Version 6.1.0 on Django 6.1 and 5.2.4 on Django 5.2, 0.3.0 (4.18.2, 8.2.12). `MongoModelSerializer` alone and with aiodrf's `ModelSerializer`: CRUD, pages, django-filter, `ObjectId` keys in URLs and bodies, 404s and 400s as in DRF, one hop; embedded models and arrays; relations with `aiodrf.contrib.mongodb`'s `ObjectIdPrimaryKeyRelatedField` or `pk_field`; `ATOMIC_SAVE` in MongoDB's transaction through `aiodrf.contrib.mongodb`, Django's no-op on a standalone server, `aiodrf.W007`; `Meta.auto_prefetch` without many-to-many prefetches; compiler declines; the async ORM with no hop ([guide](async-nosql.md)) |
| django-safedelete | destroy hides the row (`SOFT_DELETE_CASCADE`), retrieve and list stop serving it, a foreign key to it is refused, `undelete()` serves it again, as DRF's views answer; not with `aiodrf.contrib.async_backend`, whose `async_delete()` does not call the model's `delete()` |
| django-auditlog | two users' concurrent changes each attributed to their session user, masked fields, no entry for a rolled-back change; a user DRF authenticates in the view (JWT, token) is not the actor unless the view sets it (below) |
| django-prometheus | Version 2.5.0, on Django 6.0 (it declares Django < 6.1). Responses by status, view name and method; exceptions that escape DRF's handler; the latency ends when a streamed body starts; QUERY is `<invalid method>` in its response counters |
| Schemathesis | Version 4.28.0, a testing tool. Requests generated from the [bookshop example](../../examples/bookshop/README.md)'s schema, anonymous and authenticated, cause no server error, and responses match their documented schema |

djongo is not supported: version 1.3.7 declares Django 2.1 to 3.1.12, and with
Django 5.2 or 6.1 every insert fails with `SQLDecodeError`. See the
[MongoDB guide](async-nosql.md#djongo).

### Caches, search indexes and middleware

- django-cacheops invalidates through signals, one transaction at a time.
  aiodrf's save, its `transaction.atomic` and `on_commit` run in one worker
  hop, so invalidation happens at the commit, as with DRF. `QuerySet.update()`
  does not invalidate, as upstream documents (`invalidated_update()`). Its
  classifiers stop at Django 5.2 and Python 3.13, but it works with
  Django 6.1 and Python 3.14. With `aiodrf.contrib.async_backend`, native
  reads (`async_objects`) are not cached, and native writes invalidate after
  the native commit ([async backend guide](async-backend.md)).
- django-elasticsearch-dsl's signal processor calls the synchronous client,
  which is correct in aiodrf's worker thread. In an `async def` handler, use
  `elasticsearch.dsl.AsyncSearch` with `async_connections`
  (`pip install "elasticsearch[async]"`), and create and close the async
  client per event loop. Indexing happens on `post_save`, so a rolled-back
  save is indexed anyway, as under DRF. With elasticsearch-py 9.4 or later,
  django-elasticsearch-dsl 9.0 indexes empty documents; work around it by
  overriding the document's `prepare()` to return
  `{name: prepare(instance) for name, _, prepare in self.init_prepare()}`.
  django-elasticsearch-dsl-drf 0.22.5 cannot be used: it imports `distutils`,
  removed in Python 3.12.

- djangochannelsrestframework 1.3.0 wraps DRF permission classes and calls
  their synchronous `has_permission` in a thread with a Django `HttpRequest`
  built from the scope, not a DRF `Request`; aiodrf's bridge runs
  `ahas_permission` from there. It calls `asyncio.iscoroutinefunction`, which
  emits a deprecation warning on Python 3.14.
- django-health-check's view is a plain Django async view; synchronous checks
  run in the event loop's default executor, not in aiodrf's hops.
- django-idempotency-key's middleware is synchronous, so Django runs it in a
  thread around the async view. Its default in-memory storage is per process
  and replays the stored response object. Its lock covers the lookup of a
  stored response, not the request, so two concurrent requests with one key
  can both run. Use a database constraint where a duplicate write must be
  impossible.
  djangorestframework-idempotency-key 1.0.3 cannot be used: its decorator
  requires the handler to return a `Response`, so it cannot wrap an
  `async def` handler.

### Audit attribution, metrics and deletion

- **django-auditlog** reads `request.user` in its middleware, before DRF
  authenticates. A session user is there; a JWT or token user is not, and
  those changes have no actor. Set it where DRF has authenticated:

  ```python
  from auditlog.context import set_actor


  class Notes(viewsets.ModelViewSet):
      async def aperform_create(self, serializer):
          with set_actor(self.request.user):
              await super().aperform_create(serializer)
  ```

  aiodrf runs the save in a worker with the request's context, so the actor
  reaches auditlog's signal receivers there.
- **django-prometheus** times a request until its response starts: the time
  spent streaming a body (NDJSON, SSE) is not in
  `django_http_requests_latency_seconds_by_view_method`. With several
  processes (Uvicorn workers), each has its own registry; export them as
  its documentation describes for multiprocess servers.
- **django-safedelete** works with aiodrf's generic views as with DRF's. The
  native contrib deletes with `async_delete()`, which does not call a model's
  `delete()`: soft-deleting models are not supported there.

Synchronous hooks of untested packages run in the request's worker thread with
Django's context, including the active language, time zone and context
variables. This lets most DRF packages work unchanged, but it does not make
their I/O asynchronous, and the number of thread switches depends on which hooks
a package overrides. Test the packages your project uses with its own views.

### Channels and djangochannelsrestframework

A DCRF action is an `async def` on the consumer. It can await aiodrf
serializers instead of wrapping DRF code in `database_sync_to_async`:

```python
from djangochannelsrestframework.decorators import action
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer

from aiodrf import aio


class AuthorConsumer(GenericAsyncAPIConsumer):
    queryset = Author.objects.order_by("name")

    @action()
    async def create(self, data, **kwargs):
        serializer = AuthorSerializer(data=data)  # an aiodrf serializer
        await serializer.ais_valid(raise_exception=True)
        await serializer.asave()
        return await serializer.adata(), 201

    @action()
    async def list(self, **kwargs):
        serializer = PlainAuthorSerializer(self.get_queryset(), many=True)  # DRF's
        return await aio.data(serializer), 200
```

A `ValidationError` raised by `ais_valid(raise_exception=True)` reaches the
client as DCRF formats any `APIException`: `{"errors": [{"name": [...]}],
"response_status": 400}`. Payloads, statuses and error bodies are the same as
those of the action written with `database_sync_to_async`.

Each awaited step that runs DRF code is one hop. A create requires
three (validation, the save, the representation), a refused create one, a list
one; the same action inside one `database_sync_to_async` call crosses to a
thread once. Awaiting the steps pays off when the serializer has async members
(validators, `validate_<field>`, `acreate`, async `SerializerMethodField`s),
which run on the event loop between the hops. For a serializer that is
synchronous throughout, one `database_sync_to_async` around all of it is
cheaper.

Pitfalls:

- Database connections. A consumer lives as long as its WebSocket. Django
  closes connections at the end of an HTTP request, not of a WebSocket
  message; `database_sync_to_async` closes old connections around each call,
  aiodrf's hops run in asgiref's thread-sensitive executor without doing so.
  Keep `CONN_MAX_AGE = 0` (or a pool) and do not hold a transaction open
  across awaits.
- `database_sync_to_async` and aiodrf's `run_sync` are both thread-sensitive
  `sync_to_async`; use DCRF's for code you write in the consumer, and let
  aiodrf run what the serializer needs.
- After actions that awaited aiodrf's hops or the async ORM, call Channels'
  `await aclose_old_connections()` (`channels.db`), for example at the end
  of the action or in `disconnect()`, as `database_sync_to_async` would have.

### Error tracking, telemetry, Celery and S3

These packages were tested with aiodrf; aiodrf adds no adapter or dependency
for them.

| Package | Tested version | Tested behaviour and limits |
| --- | --- | --- |
| sentry-sdk | 2.70.0 | Django integration under ASGI and WSGI, isolation of concurrent requests and users, errors raised in views and in worker threads, exactly one event per error, and no false error when a client disconnects. Tested with an in-memory transport. |
| OpenTelemetry SDK, Django and ASGI instrumentation | 1.44.0, 0.65b0 | Parent-child relationships of server, view and worker spans; route, status and error attributes; context isolation under ASGI and WSGI; exporter failure; sampling. In the tested Django instrumentation, a cancelled request does not end its server span; this also happens without aiodrf. |
| Celery, redis-py | 5.6.3, 6.4.0 | Publishing after commit and not after rollback, worker placement and context, cancellation (which does not stop a publication already running), a prefork worker, a Redis outage and recovery. Remote failover, delivery durability and exactly-once execution were not tested. |
| django-storages, boto3 | 1.14.6, 1.43.100 | The S3 backend with `FileField` uploads, signed URLs, reading, deleting, access-denied errors and temporary-file cleanup, and the refresh of expired credentials. Tested without network access to AWS; IAM, STS and large multipart uploads were not tested. |

Install error-tracking and telemetry SDKs before the ASGI or WSGI handler is
created. Sentry patches Django itself; aiodrf does not patch anything for it.
The OpenTelemetry Django instrumentation needs its optional ASGI dependency to
record ASGI requests, and its middleware is synchronous, which adds thread
switches. Follow the [official instrumentation setup](https://opentelemetry-python-contrib.readthedocs.io/en/latest/instrumentation/django/django.html),
avoid enabling both the ASGI and the Django server instrumentation, and keep
exporter, privacy and sampling policies in your application.

`aiodrf.contrib.opentelemetry.TracingMixin` (extra `opentelemetry`, which needs
only the API package) adds spans for aiodrf's phases below the server span of
Django's instrumentation: `aiodrf.authenticate`, `aiodrf.check_permissions`,
`aiodrf.check_throttles`, `aiodrf.handler` and `aiodrf.finalize`. They carry the
view class and action as attributes and nothing from the request. Your own spans
in the handler, including those opened in a worker thread, are children of
`aiodrf.handler`. Expected DRF responses (a 403, a 429) leave the span status
unset and record the exception name; other exceptions set it to error; a
cancelled handler ends its span with `aiodrf.cancelled`. Validation,
representation and saving happen inside the handler span; rendering happens
after the view, in Django's handler, and has no span of its own. The mixin opens
no spans until your application configures a tracer provider.

Celery publishes synchronously. Call it from a synchronous action hook, or from
an async hook through `sync_to_async`. Keep transactional work and its
`transaction.on_commit()` registration in one synchronous function. Do not use
`asyncio.create_task()` as a task queue, and do not retry a cancelled publish
automatically: its worker thread can still send the message.

## OpenAPI and Swagger UI

Use drf-spectacular's generator and UI views. The schema is generated from the
views and serializers; publishing it is the project's decision, and aiodrf adds
no routes and changes no settings.

Settings:

```python
INSTALLED_APPS = [
    # Existing Django/project apps...
    "rest_framework",
    "drf_spectacular",
    "aiodrf",
]

REST_FRAMEWORK = {
    # Keep the application's auth, permission, renderer and filter settings.
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Example API",
    "VERSION": "1.0.0",
    "COMPONENT_SPLIT_REQUEST": True,
}
```

URLs:

```python
from django.urls import path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

urlpatterns = [
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    path(
        "api/redoc/",
        SpectacularRedocView.as_view(url_name="schema"),
        name="redoc",
    ),
    # Existing API URLs...
]
```

Redoc is optional. Namespaced applications pass the namespaced schema URL name.
Protect the schema and UI views with the application's permission policy; they
are DRF views and need no conversion to aiodrf.

In a project, validate the generated schema with:

```console
python manage.py spectacular --file /tmp/aiodrf-schema.yml --validate --fail-on-warn
```

(aiodrf's own repository has no `manage.py`; its tests use `SchemaGenerator`
and `call_command("spectacular", ...)` under the test settings.)

### Offline schema and lifespan

Schema generation and Django system checks do not start the ASGI lifespan. If
`get_serializer_class()` or `get_queryset()` depends on a live resource, return
the `swagger_fake_view` fallback before touching it. Keep that selection
synchronous, and do not open an HTTP client or start background tasks in
`AppConfig.ready()` for schema generation.
This follows [spectacular's schema-time guidance](https://drf-spectacular.readthedocs.io/en/latest/faq.html#my-get-queryset-depends-on-some-attributes-not-available-at-schema-generation-time).

The generated schema is the same before, during and after a managed lifespan,
and the schema and check commands never call the lifespan factory.

### aiodrf schema extensions

`aiodrf/contrib/spectacular/extensions.py` is imported by `AppConfig.ready()`
when drf-spectacular is installed (only the package being absent is
tolerated; an error inside it is raised). It registers aiodrf's
`SessionAuthentication`, which spectacular matches by exact class, and the
streaming response schemas. The same `ready()` imports django-fastdrf's
`fastdrf.spectacular`, which documents msgspec/pydantic serializers from
their schema classes: the input schema for requests (an explicit
`Meta.partial_schema` for PATCH), the output schema for responses, with the
names the runtime uses (`by_alias=True` for pydantic). Components are
identified by their shape. A pydantic model whose validation and
serialization shapes differ (a `serialization_alias`) gets a `<Name>Request`
component for the request side, nested or top-level; without
`COMPONENT_SPLIT_REQUEST` spectacular would not split it, so the extension
does and warns. DRF-defined serializers are documented by spectacular as
usual, whichever backend produces their output.

aiodrf's view classes and mixins have no docstrings: spectacular describes an
operation with the first docstring among the view's classes before DRF's, and
would publish a mixin's for every view without its own.

The generated OpenAPI 3.1 document validates, and real nested `POST` and
partial `PATCH` bodies validate against its components. Recursive Pydantic
roots resolve to a component definition with both older (2.7) and current
Pydantic versions.

Query serializers (`query_serializer_class`, see the
[extension-hook contract](extension-hooks.md#query-parameters)) are
documented as query parameters by `aiodrf.contrib.spectacular.AutoSchema`,
per action where `get_query_serializer_class()` depends on it:

```python
REST_FRAMEWORK = {"DEFAULT_SCHEMA_CLASS": "aiodrf.contrib.spectacular.AutoSchema"}
```

Without it, `@extend_schema(parameters=[Filters])` documents the same fields.
Views with a QUERY handler make drf-spectacular 0.30 fail: it builds a
request for every method with DRF's `APIRequestFactory`, which has no
`query()`, and OpenAPI 3.0/3.1 have no QUERY operation. Leave those
operations out with the preprocessing hook:

```python
SPECTACULAR_SETTINGS = {
    "PREPROCESSING_HOOKS": [
        "aiodrf.contrib.spectacular.hooks.preprocess_exclude_query_method"
    ],
}
```

For SSE and NDJSON, use the `StreamSchema(ItemSerializer)` annotation from
`aiodrf.contrib.spectacular`. It resolves item components without executing the
stream and does not describe the body as a JSON array. The
[streaming schema guide](streaming-schema.md) has its usage and the limits of
its `x-aiodrf-item-schema` extension, which generic OpenAPI clients may ignore.

## django-filter

Use `django_filters.rest_framework.DjangoFilterBackend`;
`aiodrf.contrib.django_filters` re-exports it for older imports. The whole
backend (the `FilterSet`, its form, `filter_queryset`, custom methods) runs
inside the action's hop, like every filter backend that is not declared pure.
DRF's `SearchFilter` and `OrderingFilter` run there as well: they call
`get_search_fields()` and build the view's serializer, which a project may
have written with queries.

### rest-filters

Use the vendor's `rest_filters.FilterBackend` and `FilterSet` directly. The
backend implements DRF's synchronous filtering protocol, so aiodrf runs it in
the existing worker boundary; no contrib adapter or monkeypatch is needed.
Serializer fields validate parameters before queryset filtering. Queryset scope
set by the view is retained. The
[data example](../../examples/ecosystem-data/README.md) exposes an explicitly
filtered list/detail route alongside the other serializer integrations.

The ecosystem tests compare DRF and aiodrf through both Django test clients and
verify drf-spectacular parameter names/types. The vendor's schema implementation
uses spectacular internals, so schema coverage is version-specific. Nested filter
groups, arbitrary application overrides and every vendor constraint combination
are not certified by these tests. See
[rest-filters](https://github.com/realsuayip/rest-filters) for its complete API.

## Authentication

DRF's authentication order and its first-success rule are kept. Session
authentication uses Django's async accessor for a lazy user and respects a
concrete `request.user` set by middleware, as DRF does. Its CSRF check is inline
unless the secret is in the session or the body is multipart. Header-based classes run
in one hop, and in none for a request that carries nothing for them:

```python
INSTALLED_APPS += ["aiodrf.contrib.simplejwt", "aiodrf.contrib.knox"]
```

Both apps only call `aiodrf.authentication.register_credentials_check`, which
any project can use for its own header-based class. The check applies while
`authenticate()` is the one the registered class defines; a subclass that
overrides it (dj-rest-auth's `JWTCookieAuthentication`) is asked in a thread.

drf-auth-kit's `JWTCookieAuthentication` and `TokenCookieAuthentication` are
such subclasses: they read the token from the `Authorization` header or,
without one, from the cookie of the request's cookie profile
(`AUTH_COOKIE_PROFILES`, chosen by `Origin`). `aiodrf.contrib.auth_kit`
registers the same two steps as their check, so a request with neither costs
no hop, whatever `AUTH_TYPE` says:

```python
INSTALLED_APPS += ["aiodrf.contrib.auth_kit"]
```

`JWTStatelessUserAuthentication` loads no user, but whether it does I/O
depends on settings (`JWK_URL` fetches keys, the blacklist app queries), so
aiodrf does not declare it pure; `aiodrf/contrib/simplejwt/__init__.py` shows
how a project does.

## Caches

DRF's rate throttles run on the event loop when their cache is Django's
`LocMemCache` or `DummyCache`, and in a thread with every other backend,
including subclasses of those two that add code. `LocMemCache` is per
process: with several workers each has its own counters, in DRF as in
aiodrf.

DRF's throttles read the request history, append to it and write it back, so
concurrent requests can all pass (DRF's documentation says so).
`aiodrf.throttling.FixedWindowRateThrottle`, with `AnonFixedWindowRateThrottle`,
`UserFixedWindowRateThrottle` and `ScopedFixedWindowRateThrottle`, keeps DRF's
rates, scopes and cache keys and counts one number per window with the cache's
`add` and `incr`:

| Backend | `incr` | Concurrent requests per window |
| --- | --- | --- |
| LocMem | under a lock | exactly the rate, per process |
| Django's Redis, django-redis | atomic on the server | exactly the rate |
| Memcached (not in the test matrix) | atomic on the server | exactly the rate |
| Database, file | read and write | approximately the rate, as with DRF |
| Dummy | nothing is stored | not limited, as with DRF |

The window starts at a multiple of its duration, not at a client's first
request: a client can send the rate at the end of one window and again at the
start of the next. The counter's key names the window and expires a second
after it, so Django's Redis backend, whose `incr` is `EXISTS` then `INCR`,
cannot leave a counter without an expiry behind. With LocMem it runs on the
loop; with `DummyCache`, whose `incr` is `BaseCache`'s, and with every remote
backend, it runs in one hop together with the other throttles.

Django's `cache_page` looks the response up with a synchronous `cache.get()`
before it awaits an async view. On the event loop that blocks for a round
trip to Redis or Memcached, and raises `SynchronousOnlyOperation` with the
database cache.
`aiodrf.cache.cache_page` is the same decorator with the lookup in a thread
unless the cache is in-process; keys, `Vary` and `Cache-Control` are
Django's. `vary_on_headers`, `vary_on_cookie`, `cache_control` and
`never_cache` do no I/O; use Django's. `aiodrf.cache.cache_page` and the
throttles are tested with LocMem, Dummy, database, file, Django's Redis backend
and django-redis.

## Django features

aiodrf views are tested with every session engine, with and without
`CSRF_USE_SESSIONS`; with JSON bodies held in memory and spooled to disk,
multipart uploads, malformed JSON, unsupported media types and
`DATA_UPLOAD_MAX_MEMORY_SIZE`; with `LocaleMiddleware` translations and an
activated time zone inside the worker thread; behind a synchronous-only
middleware; with async streaming responses; and with database routers,
including the database `ATOMIC_SAVE` opens its transaction on.
`ATOMIC_REQUESTS` cannot be used with async views: Django raises an error, and
the system check `aiodrf.W002` reports it at start-up.

Django's stock middleware works unchanged: security, common and frame-option
headers and redirects, messages with signed-cookie sessions, the session CSRF
token and origin and referer checks, `RemoteUserMiddleware` and
`LoginRequiredMiddleware`, `GZipMiddleware` with `ConditionalGetMiddleware`,
ETags and `HEAD`, and Server-Sent Events heartbeats, backpressure and
disconnects. Two limits come from Django itself:

- Django 5.2 can keep a stale anonymous `request.auser()` result after
  `alogin()`. aiodrf's session authentication uses the user the middleware set
  and is not affected. See [Django #37042](https://code.djangoproject.com/ticket/37042).
- In Django 6.0, the async `GZipMiddleware` buffers small chunks, so
  Server-Sent Events and heartbeats may be delayed. Django 5.2 and 6.1 are not
  affected. On 6.0, exclude `text/event-stream` from compression in your
  project or proxy, for example with a `GZipMiddleware` subclass that skips it.
  See [Django #36293](https://code.djangoproject.com/ticket/36293).

Custom middleware orders are not covered by these tests.

Django's task framework (Django 6.0 and later), and `django-tasks` 0.12 on
Django 5.2, work from aiodrf views as from Django's own async views: use
`aenqueue()` in async code. `ImmediateBackend` runs a synchronous task in the
request's thread and an `async def` task on the event loop. Calling the
synchronous `enqueue()` from a coroutine records Django's
`SynchronousOnlyOperation` as the task's failure. See [Django Tasks](tasks.md).

Multipart uploads, including files Django spools to disk, are parsed and saved
to storage within the action's hop.

All of these features are also tested on PostgreSQL, including what SQLite
cannot show: a failed statement inside `ATOMIC_SAVE` leaves the connection
usable, and `on_commit` callbacks, failing signal receivers and nested
`atomic()` blocks behave as in Django.
