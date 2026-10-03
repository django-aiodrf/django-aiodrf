# Changelog

User-visible changes are recorded here in Keep a Changelog format.
Version 0.0.1 is the initial alpha. Development revisions are
consolidated here rather than represented as separate published releases.

## [Unreleased]

## [0.0.3] - 2026-10-03

Timings in this section were measured on the machine described in
[the performance guide](https://github.com/django-aiodrf/django-aiodrf/blob/main/docs/guides/performance.md#environment-of-the-published-measurements);
other hardware gives other times.

### Upgrading

aiodrf now depends on [django-fastdrf](https://github.com/ctolon/django-fastdrf)
0.4, which maintains the serializer optimizations aiodrf used to carry a copy
of. Their settings are read from django-fastdrf's `FASTDRF` setting: move
`SERIALIZER_BACKEND`, `SERIALIZER_BACKEND_PARITY`,
`SERIALIZER_BACKEND_FALLBACK`, `ALLOWED_SERIALIZER_BACKENDS`,
`CACHE_SERIALIZER_FIELDS`, `FIELD_COPY_MODE`, `BATCH_RELATED_LOOKUPS` and
`FETCH_MODE` from `AIODRF` to `FASTDRF`. One of them left in `AIODRF` raises
`ImproperlyConfigured` when the settings are first read.

The code is django-fastdrf's, and aiodrf no longer re-exports it: import it
from django-fastdrf.

| Before | Now |
| --- | --- |
| `aiodrf.contrib.msgspec.renderers.MsgspecJSONRenderer` (in `REST_FRAMEWORK`) | `fastdrf.msgspec.renderers.MsgspecJSONRenderer` |
| `aiodrf.contrib.msgspec.parsers.MsgspecJSONParser` (in `REST_FRAMEWORK`) | `fastdrf.msgspec.parsers.MsgspecJSONParser` |
| `MsgspecJSONRenderer`, `MsgspecJSONParser` from `aiodrf.contrib.msgspec` | the two modules above |
| `aiodrf.response.DataResponse` | `fastdrf.response.DataResponse` |
| `aiodrf.prefetch`, `aiodrf.contrib.builtin.prefetch` | `fastdrf.prefetch` |
| `aiodrf.contrib.cache_codecs.PydanticCodec` | `fastdrf.codecs.PydanticCodec` |
| `aiodrf.contrib.compiler`, `inputs`, `convert` | `fastdrf.compiler`, `fastdrf.inputs`, `fastdrf.convert` |
| `aiodrf.contrib.msgspec.compiler`, `aiodrf.contrib.pydantic.compiler` | `fastdrf.msgspec.compiler`, `fastdrf.pydantic.compiler` |
| `FieldSpec`, `BoundedCache`, `SCHEMA_CACHE_SIZE`, `SchemaBackend`, `build_serializer`, `error_detail`, `is_schema_class`, `raise_validation_error` from `aiodrf.contrib.typed` | `fastdrf.typed` |
| `serializer_kind`, `require_allowed` from `aiodrf.backends` | `fastdrf.typed` |
| `MsgspecBackend`, `PydanticBackend` from `aiodrf.contrib.*.serializers` | `fastdrf.msgspec.serializers`, `fastdrf.pydantic.serializers` |
| `WeakChildMixin`, `bind_child_weakly` from `aiodrf.contrib.list_serializers` | `fastdrf.list_serializers` |

The native Redis and Valkey caches and the ASGI lifespan support are
separate distributions now, which projects without DRF can use too:
[aiodrf-async-cache](https://github.com/django-aiodrf/aiodrf-async-cache) and
[aiodrf-asgi-lifespan](https://github.com/django-aiodrf/aiodrf-asgi-lifespan).
Install the first, and aiodrf's `lifespan` extra for the second. The
`AIODRF["LIFESPAN"]` setting is now the top-level `DJANGO_LIFESPAN` setting.

| Before | Now |
| --- | --- |
| `aiodrf.contrib.redis.AsyncRedisCache` | `aiodrf_async_cache.redis.AsyncRedisCache` |
| `aiodrf.contrib.valkey.AsyncValkeyCache` | `aiodrf_async_cache.valkey.AsyncValkeyCache` |
| `aiodrf.contrib.valkey.LifespanConnectionFactory` | `aiodrf_async_cache.django_valkey.LifespanConnectionFactory` |
| `AsyncCacheMiddleware`, `AsyncFetchFromCacheMiddleware`, `AsyncUpdateCacheMiddleware`, `AsyncCache` from `aiodrf.contrib.async_cache` | `aiodrf_async_cache.middleware` |
| `aiodrf.contrib.async_cache.cache_lifespan` | `aiodrf_async_cache.lifespan.cache_lifespan` |
| `aiodrf.contrib.cache_codecs.MsgspecCodec` | `aiodrf_async_cache.codecs.MsgspecCodec` |
| `aiodrf.signals.asgi_startup`, `asgi_shutdown` | `aiodrf_asgi_lifespan.signals` |
| `aiodrf.asgi.get_lifespan_state`, `LifespanApplication` | `aiodrf_asgi_lifespan.asgi` |
| `aiodrf.test.lifespan` | `aiodrf_asgi_lifespan.testing.lifespan` |
| `AIODRF["LIFESPAN"]` | `DJANGO_LIFESPAN` |

`aiodrf.asgi.get_asgi_application()` stays: it answers the ASGI lifespan
protocol as before, sends the startup and shutdown signals when
aiodrf-asgi-lifespan is installed, and adds `AIODRF["REQUEST_THREADS"]`.
`AsyncAPIClient(lifespan=...)` takes the state that
`aiodrf_asgi_lifespan.testing.lifespan()` yields.

The other `aiodrf.contrib.builtin` modules of field caching, field copying,
related lookups and the python backend are django-fastdrf's internals.
`manage.py aiodrf_convert` is gone: add `"fastdrf"` to `INSTALLED_APPS` and run
`manage.py fastdrf_convert`, which converts aiodrf's serializers too.

### Added

- django-fastdrf's `fastdrf.registry` and its `fastdrf.contrib`
  applications (django-phonenumber-field, django-countries, django-money)
  compile the fields of other packages and of the project in aiodrf's
  serializers too, synchronous and asynchronous alike.
- django-fastdrf's delegated fields (`FASTDRF["DELEGATE_FIELDS"]`,
  `Meta.delegate_fields`): `SerializerMethodField` and the other fields a
  backend cannot compile run their own code inside the compiled output. In
  aiodrf's views that output is produced where DRF's representation would
  run: in a worker thread with `REPRESENTATION_MODE = "thread"` (the
  default), since a delegated field runs the project's code.
- aiodrf registers django-fastdrf's `fastdrf.I001` hint with its settings
  check when `"fastdrf"` is not in `INSTALLED_APPS`.
- A renderer registered with `fastdrf.registry.register_data_renderer()`
  renders on the event loop only when it is declared pure
  (`register_pure`, `INLINE_RENDERERS`); otherwise in a thread, like any
  other renderer of the project's.
- django-fastdrf's `PydanticJSONParser`, `PydanticJSONRenderer`,
  `ORJSONParser` and `ORJSONRenderer` work in asynchronous views,
  `DataResponse` and streamed responses, through DRF's `PARSER_CLASSES` and
  `RENDERER_CLASSES`. The `orjson` extra installs orjson.

### Changed

- The compiler, input recognition, the converter, field caching and copying,
  related lookups and auto-prefetch are django-fastdrf's; their
  `aiodrf.contrib` modules are gone (see Upgrading).
- django-fastdrf's system checks report the `FASTDRF` settings:
  `fastdrf.E004` replaces `aiodrf.E004` (a backend that is not installed),
  and a `FETCH_MODE` on Django before 6.1 is the error `fastdrf.E006`
  instead of the warning `aiodrf.W009`.
- The schema serializers (`MsgspecSerializer`, `PydanticSerializer`,
  `SchemaSerializer`, `adapt()`), most of
  `manage.py aiodrf_inspect_serializers`, the class caches and the
  serializer field caching are django-fastdrf's, on aiodrf's asynchronous
  bases. An error about a pydantic.v1 model or an `ALLOWED_SERIALIZER_BACKENDS`
  mismatch now names django-fastdrf's serializers and `FASTDRF`.
- The msgspec renderer, the JSON renderer `DataResponse` and the event loop
  render DRF's renderer with, and `DataResponse` are django-fastdrf's. With
  `indent=0` in the renderer context, `MsgspecJSONRenderer` now renders DRF's
  bytes, as for any other indentation, instead of compact output.
- `manage.py fastdrf_convert` (formerly `aiodrf_convert`) marks what it did
  not convert with `# TODO(convert): ...` and names the converter, not the
  command, in the generated module's docstring.
- `MsgspecJSONRenderer` looks for a single byte before searching the output
  for non-finite numbers and the U+2028/U+2029 separators: rendering 1,000
  rows takes about a third of the time when neither is present.
- Input recognition checks that the request body holds only JSON types
  without a call per value, skips the surrogate search for ASCII strings, and
  finishes list, dictionary and nested values with less work per item:
  recognizing a 100-line body takes about 40% less time.
- The msgspec backend converts datetime and decimal columns at once, reading
  the time zone and the decimal context once per column. Values whose
  conversion could run project code (another time zone class, a string
  subclass) are still converted row by row, in DRF's order. A list of 1,000
  rows of seven fields, one datetime and one decimal, takes about 25% less
  time.
- The drf-spectacular extension that documents the schema serializers
  (`MsgspecSerializer`, `PydanticSerializer`, `SchemaViewMixin` views) is
  django-fastdrf's `fastdrf.spectacular`; aiodrf's application installs it
  with its own extensions, so a project changes nothing.

### Fixed

- With `REQUEST_THREADS`, the request threads are closed before the
  lifespan's resources, which code running in them may still use.
- With django-fastdrf 0.4, `QueryOptimizationMixin` no longer joins a
  relation that the view's queryset prefetches with a filtered `Prefetch`:
  the join made Django skip the `Prefetch`, and rows its filter left out
  (another user's related object) were in the response. Nor does it join a
  foreign key the queryset defers (`only()`, `defer()`, also by its column
  name), which raised `FieldError`, nor derive a join for a field reading a
  foreign key's column (`author_id`).
- With django-fastdrf 0.4, `BATCH_RELATED_LOOKUPS` reports DRF's validation
  error for a relation queryset that finds nothing (`none()`) instead of
  raising `EmptyResultSet` on large input, leaves querysets with a window
  annotation to DRF (their values are computed per item), and looks a
  repeated item up as DRF does, so that its mutable values are its own.
- With django-fastdrf 0.4, input recognition leaves to DRF a serializer
  whose instance (or a nested list serializer's) was given a validation
  hook of the project's, such as `run_child_validation` or `set_value`.
- `.data` of a msgspec or pydantic serializer after `is_valid()` is what
  `validate()` returned, as in DRF, not the input read before it; a list
  no longer runs the schema's `__post_init__` a second time.
- With a compiled backend, a list serializer whose own representation is
  awaited (`PrefetchListSerializer.aprefetch`, a `ConcurrentListSerializer`)
  was skipped when its child compiled: the compiled output was returned
  without the prefetch or the per-item serializers.
- With `OAS_VERSION` 3.1, the schema of a msgspec `Literal` or `Enum` field
  crashed drf-spectacular's enum postprocessing (`KeyError: 'type'`); enum
  schemas now carry their JSON type.
- drf-spectacular no longer publishes the docstring of `SchemaViewMixin` as
  the description of every operation of a view without its own docstring.

## [0.0.2] - 2026-10-01

### Fixed

- `aiodrf_inspect_serializers` reports a schema serializer as its schema's
  (`schema: msgspec validates and represents it`) instead of the reasons the
  backend would decline it for, and inspects `SchemaViewMixin` views and bare
  schema `serializer_class` declarations through the serializer they build
  instead of listing them as not inspected.
- The `pydantic` extra requires pydantic 2.9 or later. With pydantic 2.7 and
  2.8, `PydanticCodec` returned a cached `bytes` value as its base64 text,
  because those versions ignore the `val_json_bytes` setting it relies on.
  The minimum-versions test session now installs `redis`, so the cache codec
  tests run against the floors.

## [0.0.1] - 2026-09-30

### Added

- `AIODRF["SERIALIZER_BACKEND"] = "python"`: compiled output without msgspec
  or pydantic, with the same analysis, strict parity and declines; input is
  validated by DRF.
- The compiled output of a generic view's `create` and `update` uses the
  serializer class's compiled class when the view built, validated and saved
  the serializer with framework code alone, instead of looking it up by the
  instance's fields on every request.
- `get_object` checks plain synchronous object permissions with a plan made
  once per view class instead of classifying the permission instances on
  each request.
- Compiled serializers (`SERIALIZER_BACKEND`) in strict parity also compile
  datetimes and decimals output as strings, with DRF's output for any value
  (time zone, precision, rounding and errors); the primary keys and slugs of
  forward foreign keys, many-to-many fields and reverse foreign keys;
  `ModelField` (generated fields); file and image fields; dotted sources
  through foreign keys that cannot be null (`CharField(source="author.name")`);
  values of the instance alone, such as annotations; and, with
  `aiodrf.contrib.mongodb`, `ObjectId` fields and relations.
  With msgspec, several fields may read the same attribute or relation.
  Datetimes and decimals read the time zone and the decimal context once per
  output, and datetimes are formatted by the backend.
- System check `aiodrf.W009`: `FETCH_MODE` set on a Django without fetch
  modes (before 6.1), where nothing enforces it.
- System check `aiodrf.W010`: a DRF view using a DRF serializer with async
  members, which DRF's `is_valid()`, `save()` and `.data` leave unawaited
  (the coroutine object was stored in the row) or never call (`acreate()`).
- `docs/reference/checks.md`, every system check id; the conditions that
  shared `aiodrf.E003` have their own ids: `E006` (a setting's value has the
  wrong type or form, `LIFESPAN` included) and `E007` (an import string that
  names no class). `E003` is only "`AIODRF` is not a mapping".
- `aiodrf.decorators` exports DRF 3.17's `versioning_class`,
  `metadata_class` and `content_negotiation_class` in `__all__`.
- `docs/reference/upstream-internals.md`: the private Django and DRF names
  aiodrf relies on. Compatibility with DRF's viewsets is now checked by
  sending the same requests to both and comparing status, headers, body and
  database state; see [compatibility](docs/guides/compatibility.md).
- `Typing :: Typed` classifier; every function of the package is annotated
  and checked with `disallow_untyped_defs`.

- `AIODRF["REQUEST_THREADS"]` (opt-in): `aiodrf.asgi.get_asgi_application()`
  keeps the threads that run requests' synchronous code, one request at a
  time, instead of Django's two thread starts per request.

- `aiodrf.response.DataResponse` (opt-in): Django's `HttpResponse` rendered by
  the view with DRF's JSON renderers, with DRF's status, content and headers but
  without DRF's template response, keeping its content only once rendered;
  other renderers get DRF's `Response`.

- Optional django-valkey native async cache with lifespan-owned pools, isolated
  cache contracts, and examples comparing Django and native NoSQL access paths.
- Opt-in redis.asyncio backend, instance-local pool reuse and native page-cache
  middleware that delegates cache policy to Django. Native cache tests cover
  sync interoperability, cancellation, loop ownership and resource cleanup.
- Awaited native-cache key, codec and default callbacks; optional msgspec and
  Pydantic value codecs; Redis/Valkey Sentinel and Cluster configurations with
  explicit cross-slot batch and pool-capacity contracts.
- Cache reconnection and opt-in driver-retry examples, with service tests for
  accepted-write reply loss, bounded retries and cancellation during backoff.
- OpenSearch document preparation through django-opensearch-dsl, native
  opensearch-py reads/writes/bulk, isolated service tests and a Docker example.
- Awaitable DRF views, generic actions, viewsets, serializer operations and
  authentication/permission/throttle hooks with synchronous compatibility.
- Async serializer factories and authentication-header hooks, preserving
  inherited DRF behavior and synchronous callers.
- Async request access, test clients, query validation, conditional responses,
  fixed-window throttling and explicit HTTP QUERY support.
- NDJSON, JSON-array and SSE streaming with cleanup and optional OpenAPI item
  schemas; typed ASGI lifespan contexts and async management commands.
- Opt-in msgspec/Pydantic serializers and compiled DRF serializer operations,
  global/per-serializer selection, strict/fast parity and eligibility reports.
- Native schema context, custom msgspec decode/encode/OpenAPI hooks, Pydantic
  RootModel and scalar output, and msgspec array-like output. Schema metadata is
  cached separately from request-bound fields and callback context.
- Recursive canonical input recognition for typed lists, dictionaries and HStore;
  ISO time support and strict Pydantic date/UUID text conversion with DRF fallback
  for unsupported rules. Independent backend data-type and fallback matrices.
- Static field-template caching, default-off scalar field cloning and batched
  primary-key relation validation with DRF fallback.
- Recursive compiled field-copy plans with serializer/view/project selection
  and conservative custom-field fallback; no global field patch.
- ASGI static-file contracts and a ServeStatic deployment example alongside
  the isolated WhiteNoise WSGI profile.
- Optional dual-mode WhiteNoise adapter with worker lookup and cancellation
  cleanup; the vendor's synchronous file iterator remains documented.
- Batch list enrichment and experimental bounded concurrent representation.
- Django/DRF ecosystem integration contracts, including django-filter,
  rest-filters, drf-spectacular, Guardian, cache backends, vendor authentication, storage,
  OpenTelemetry, Channels and database adapters.
- Django 5's optional django-tasks backport and Django 6's built-in Tasks API
  examples and shared enqueue/transaction tests.
- Explicit ADRF migration tooling and compatibility mode; isolated optional
  native PostgreSQL and MongoDB adapters.
- Independent uv examples, settings reference, application testing, tuning,
  DRF integration and contribution guides.
- Shared Docker recipes for all examples, with isolated dependencies, loopback
  listeners and local PostgreSQL, MongoDB replica-set, cache and search services.
  A consolidated NoSQL guide distinguishes Django integration from native I/O.
- Optional Granian dependency and real-ASGI-server contracts for lifespan,
  database requests, parsing and stream disconnects; server selection and
  PostgreSQL pool sizing guidance.
- Contribution, security and AI contribution policies; releases are published
  to PyPI with trusted publishing.

- `aiodrf_asgi_lifespan.testing.lifespan()` runs the application's lifespan around a test, and
  `AsyncAPIClient(lifespan=...)`/`AsyncAPIRequestFactory(lifespan=...)` give each
  request a copy of its state, so views using `get_lifespan_state()` can be
  tested with the test clients.

- `aiodrf.contrib.list_serializers`: list serializers (and the msgspec and
  pydantic serializers' `SchemaListSerializer`) whose child refers to the list
  weakly, so the list and its instances are freed by reference counting.
- `AIODRF["MONKEYPATCHES"]` and `aiodrf.contrib.monkeypatches`: opt-in,
  named and reversible patches of DRF classes for the process
  (`weak_list_children`, `release_drf_responses`, `cache_model_field_info`,
  `keep_json_encoders`).

### Compatibility

- Viewsets follow the request plan (aiodrf's `ViewSetMixin` sets `action`
  in it, as DRF does); permissions, authenticators and rate throttles are
  classified once per class, the decisions dropped with the purity and
  override answers they rest on. `field_options` reads a view's options
  without `inspect.getattr_static`, and a list serializer represents its
  items without the child's per-item wrapper. Each request does less work,
  most noticeably on viewsets and on views with simple permissions,
  authentication or throttles.
- A closed aiodrf `Response` also releases the serializer whose `ReturnList`
  or `ReturnDict` it returned: its cached `fields` (built again if read) and
  its list's reference in the child (a weak proxy afterwards).
- A view that leaves its request lifecycle to the framework follows a request
  plan decided once per class: the same objects and state as DRF's steps,
  without per-request hook resolution. Overrides, instance settings and class
  lists changed at runtime keep the generic path.
- Payloads of exact built-in types are checked for inline rendering at C speed
  (`marshal`), DRF's `ReturnList`/`ReturnDict` as their built-in counterparts
  when they hold nothing but their serializer; DRF's `JSONRenderer` reuses the
  encoder its `render` builds.
  Rendered bytes and the worker boundary for other values are unchanged.
- A closed aiodrf `Response` cuts the back-references among DRF's request
  objects, so the view, request, body and payload are freed by reference
  counting; `data` and the renderer context's view and request stay readable.
- Class metadata caches are keyed by class identity with weak references,
  without `WeakKeyDictionary` lookups on every hit.
- In thread mode (`REPRESENTATION_MODE`, the default), DRF's representation of
  model instances whose static serializer reads only what they have loaded
  (columns, `select_related` and `prefetch_related` caches) runs on the event
  loop, without the thread hop; up to 32 objects are checked.
- The check that a representation holds no coroutine looks only at the fields
  that can pass one on, classified once per serializer instance.
- `aiodrf.contrib.async_backend` generic views represent the rows a native
  read or create loaded on the event loop when the serializer is declarative,
  the view's serializer factory (and save, for a create) is the framework's
  and the representation reads only what the rows loaded: a list and a
  retrieve without a thread hop, a create with one.
- `MsgspecJSONRenderer` skips `get_indent` when the media type has no
  parameters and the context no `indent` (same bytes).
- Input recognition answers a serializer whose fields are a function of its
  class once per class, without building its fields on every request; whether
  a serializer class builds its fields with framework code alone is decided
  once per class.

- Built-in JSON renderer instances with instance-level customizations use the
  worker, including streaming output. Payload inspection alone cannot establish
  the safety of a replaced method or encoder.
- Loaded inline serializer output avoids duplicate async-hook classification.
  Compiled msgspec relation plans skip scalar-only child traversal while retaining
  manager access, ordering and fallback behavior. Temporary schemas remain collectible.
- Conditional requests recognize ETag/Last-Modified hooks installed on view instances.
  Default async descriptors are bound once; sync/async selection and worker
  boundaries remain unchanged.

- Lifespan resource lookup avoids temporary default dictionaries; shutdown
  releases resources held through stale request-state copies, including when
  context cleanup raises.

- Empty built-in permission collections avoid policy classification after the
  factory runs; custom factories and collection behavior remain unchanged.
  Input recognition checks instance overrides without repeated dictionary reads.

- JSON payload inspection rejects custom metaclass callbacks before rendering;
  render method binding reuses marked functions without retaining responses.
- Native coroutine hooks avoid repeated general callable inspection while
  retaining synchronous-wrapper offloading and marked-callable support.
  Compiled msgspec output reuses deferred relation plans within each batch.
- Context-manager return annotations use `Generator`; the decorators themselves
  remain supported. Hop diagnostics stop recording on scope exit and inherited
  task contexts no longer own completed counters.
- Class registrations use weak references. Class metadata and compiled cache
  buckets have explicit capacity limits, including validator backreferences;
  unsupported input validator limits are not retained in cache signatures.
- Unsupported input fields, including nested and relation fields, fall back to
  DRF before compiler cache-key construction. Each serializer instance is
  checked independently, preserving dynamic field replacement and DRF errors.
- Prefetch metadata reads avoid the publication lock; table replacement prevents
  an in-flight inspection from restoring entries after invalidation.
- The distribution is named `django-aiodrf`; Python imports remain `aiodrf`.
- Optional field-copy, relation-batching and prefetch implementations are grouped
  in `aiodrf.contrib.builtin`; public prefetch compatibility imports remain.
- Inferred prefetch plans no longer reuse the first request's dynamic field tree.
- Python 3.12–3.14 and the documented Django/DRF support matrix.
- Alpha API: minor releases may change public behavior with release notes.
- Standard Django ORM async entry points retain Django's worker adaptation.
  Native database execution requires the separate, explicitly selected adapter.
- Ordinary DRF classes and project settings remain usable. Optional compiler
  and fast-parity behavior is not enabled by default.
- No package has been published by this development update.

### Fixed

- With `SERIALIZER_BACKEND_FALLBACK = "error"`, msgspec and pydantic
  serializers raised `ImproperlyConfigured` as if they could not be compiled;
  they output with their own schema. An exception raised while the compiled
  class read an attribute (a database error, a query a profiler refuses) was
  reported as `ImproperlyConfigured`; it is raised as itself. An instance the
  compiled class cannot read (an unsaved instance's related objects, a deleted
  related row, a missing annotation) raised under `"error"`; it is DRF's
  output whatever the fallback.
- Compiled input skipped the defaults of read-only fields, which DRF calls at
  every validation; a serializer with a callable read-only default, such as
  `CurrentUserDefault()`, is validated by DRF.
- A `ModelSerializer` related field whose source is an async model property
  or method answered with an unawaited coroutine; the source is awaited and
  the field answers as DRF does for a synchronous one.
- `AsyncAPIClient` with `follow=True` crashed without `format` and sent
  `format` as a header of the followed request; `logout()`/`alogout()` kept
  `credentials()` and `force_authenticate()`, which DRF's client clears.
- `python -m aiodrf.codemod` stopped at the first file it could not parse;
  it names the file, converts the others and exits with status 2.
- `aiodrf_convert` generated `Literal[]` for a `ChoiceField` without choices
  and a class without a body for a schema of notes only; `--output` writes
  UTF-8 whatever the locale.
- `get_lifespan_state()` raised `TypeError` for `scope["state"] = None`.
- A view declaring its parser, renderer or authentication classes as tuples
  never took the request plan.
- A viewset instantiated without `as_view(actions)` raises DRF's
  `AttributeError` for `action_map` under the request plan too, instead of
  running without an `action`.
- With `REPRESENTATION_MODE = "inline"`, a row a native (async_backend) view
  creates is represented in the save's worker again unless its serializer
  reads only loaded values: its to-many relations queried on the event loop.
- `MsgspecJSONRenderer` fails on a value it cannot encode with DRF's
  `TypeError` ("Object of type X is not JSON serializable") instead of
  `NotImplementedError`.
- `AsyncAPIClient(HTTP_AUTHORIZATION=...)`, `AsyncAPIClient(headers=...)`
  and the same arguments of `AsyncAPIRequestFactory` send the header under its
  name; Django's async factory sent it as `Http-Authorization`, so the view
  saw no credentials.
- A serializer's synchronous member called on the event loop for an async
  implementation (`super().to_representation()` inside
  `ato_representation`) raises a `RuntimeError` naming the member and the
  async one to await, instead of asgiref's generic message.
- `MsgspecJSONRenderer` renders a dict with a `True`, `False` or `None` key
  as DRF does (`{"true": 1}`) instead of failing with `TypeError`, and a
  `Decimal` NaN or infinity fails with DRF's `ValueError` instead of writing
  a bare `NaN` token that JSON parsers refuse.
- A `BaseSerializer` that is not a `Serializer` is validated by its own
  `to_internal_value` under `SERIALIZER_BACKEND = "msgspec"`/`"pydantic"`;
  input recognition failed with `AttributeError`.
- `aio.is_valid()`/`aio.save()` no longer waive the guard against DRF's
  unawaiting `is_valid()`/`save()` when a DRF class precedes aiodrf's bridge in
  the MRO.
- `ATOMIC_SAVE` opens its transaction where a list update's instances are
  saved, one `atomic` per database for instances loaded from several; an
  unevaluated queryset is not evaluated for this.
- A pydantic v1 model given as a view's serializer is refused with a
  configuration error instead of silently becoming no serializer.
- A coroutine nested in a list or dict returned by a method field is refused
  with aiodrf's `TypeError` instead of reaching the renderer.
- A descriptor-valued class attribute (such as an `encoder_class` descriptor)
  makes a subclass's inherited methods non-pure, so they no longer run inline.
- Authenticators, permissions and throttles with `authenticate`,
  `enforce_csrf`, a permission hook or a rate-throttle hook set on the object
  itself no longer take class-level shortcuts: the method actually set runs,
  off the event loop when it is synchronous project code.
- `aiodrf.contrib.permissions`: awaiting `ahas_permission` directly consults a
  subclass's `has_permission`, `get_required_permissions` or `_queryset`.
- Hook checks refuse async functions passed to `as_view()` for hooks that must
  be synchronous, and hooks defined behind a descriptor such as a property.
- The fixed-window throttle counts a request whose window key expired between
  `add` and `incr`.
- `AsyncAPIClient.post/put/patch/delete/options/query` honour `follow=True`;
  the client's and `AsyncAPIRequestFactory`'s `generic()` accept
  `HTTP_*`-style headers.
- `Request.adata()` wraps a parser's `AttributeError` as DRF does.
- Keepalive streaming: an error raised while closing the source no longer
  hides the iteration's error; it is added to it as a note.
- A throttle class with its own `__new__` is built in a worker.
- `Accept-Query` keeps media-type parameters whose quoted values contain `;`
  or escaped quotes.
- The pydantic cache codec round-trips NaN, ±inf and bytes; models keep their
  own configuration and a given `TypeAdapter` is used as is.
- The field cache keeps unique-validator messages the project declared or set
  through `extra_kwargs`; compiled field copy keeps aliased constructor
  arguments of bound child fields; field options read the view from a
  dict-subclass serializer context.
- Automatic prefetching no longer lets a string lookup already on the
  queryset override a `Meta.prefetch` `Prefetch`, and adds no
  `select_related` join under one.
- `BATCH_RELATED_LOOKUPS` leaves `.values()`/`.values_list()` querysets to DRF,
  and deduplicates and splits `pk__in` lookups to fit the database's parameter
  limits.
- Compiled output no longer reuses an encoder across instances whose
  `ChoiceField` choice keys have different types.
- Input recognition declines list serializers with validation hooks assigned
  on the instance, and keeps `time.fold`.
- Derived pydantic partial schemas are refused when a field sets
  `validate_default=True`; a missing field read through a multi-segment
  `AliasPath` is reported where the input stops.
- Schema serializers with `Meta.model` set to-many relations after saving in
  `create()`/`update()`, as DRF's `ModelSerializer` does.
- The codemod no longer enters skipped directories while scanning, rewrites a
  symlinked file through its link, marks a missing final newline in `--diff`,
  rewrites imports that share a line or sit in one-line blocks, keeps a module
  alias used other than through its attributes, and leaves functions nested
  inside view methods unrenamed.
- System checks: a URLconf that includes itself no longer hangs the URL walk;
  W008 reports async-only policies that inherit DRF's synchronous member from
  a DRF policy such as `AllowAny`.
- adrf compatibility: adrf's concrete generic views derive from its
  `GenericAPIView`; the router keeps actions a viewset defines under DRF's
  names; `from adrf import x` always registers `adrf.x` and warns; a warning
  turned into an error fails every import; `uninstall()` removes only its own
  aliases.
- `aiodrf_convert`: recursive references, literals that cannot be written as
  source, non-finite floats, `dict` minimums above one, nullable items in
  nested lists and settings next to recognised constraints are marked with
  TODOs instead of producing broken or looser code; nested instances of one
  serializer class with different fields no longer share a schema; a
  serializer's own initialisation error is reported as itself.
- `aiodrf_inspect_serializers` reports a missing serializer backend in each
  direction (`backend_not_installed`).
- drf-spectacular: a query serializer getter returning `None` adds no
  parameter; a derived PATCH body no longer replaces the full request
  component; a renamed request component no longer overwrites an existing
  `<Name>Request`; OpenAPI 3.0 conversion no longer rewrites field names that
  match JSON Schema keywords.
- async_backend: `afilter_queryset`/`filter_queryset` overrides are applied;
  the project's database routers are asked for write aliases in a worker for
  create, update, to-many set and destroy; `aset_many()` accepts a Django
  queryset (evaluated in a worker) as well as a native one.
- opensearch: a failed `abulk()` closes the async iterator of instances it was
  consuming.
- whitenoise: an error while closing a late response no longer replaces the
  request's cancellation.
- management: SIGINT/SIGTERM keeps its outcome when the handler or its context
  managers raise while being cancelled; that error is kept as the context.
- Serializers with async hooks validated on the event loop what may query:
  callable defaults of read-only and nested fields, a nested serializer's
  `get_value()`/`validate_empty_values()` overrides and a field's own
  `run_validators()`. They now run in the worker, as without async hooks.
- `ATOMIC_SAVE` asked the project's database routers on the event loop; the
  write's alias is now chosen in the worker, with the write.
- An `async def permission_denied()` or `throttled()` returned a coroutine DRF
  never awaited, so a denied request reached the handler. Both are now rejected
  by `as_view()`, like DRF's other synchronous hooks.
- A user set by the view's own code (`request.user = ...` in
  `perform_authentication`) was replaced by authenticating again before
  permissions and throttles; it is now kept, as DRF keeps it.
- Streaming responses rendered a `None` item as nothing (`[,1]`, an empty
  NDJSON line, `data: `); it is now `null`.
- `aget_serializer_context()` no longer spends a thread hop on DRF's default,
  which builds a dictionary.
- The msgspec input recognizer rounded fractional seconds beyond microseconds,
  so "23:59:59.9999999" could become midnight; times are now truncated as
  Django's `parse_time` truncates them.
- Strict parity compiled `ReadOnlyField` and `PrimaryKeyRelatedField` values DRF
  outputs unchanged (UUIDs, dates, an int in a `FloatField`) into strings or
  floats; such fields now compile only for string, integer and boolean values.
- Strict parity re-formatted UUID, date and time values still held as text
  (`Model(code="...")`); they are now output by DRF's own conversion.
- `CACHE_SERIALIZER_FIELDS` kept the message of generated unique validators in
  the language of the request that built the class template, and deep-copied
  the model managers DRF passes to the relation fields it builds (failing for
  managers holding uncopyable state). Messages now follow each request's
  language and every copy shares the managers, as DRF does.
- Native Redis/Valkey cache aliases no longer break `manage.py check` and other
  commands that run Django's cache checks: the first event loop to use the
  backend owns it.
- Native caches no longer inherit the drivers' Cluster command retries, which
  could apply a timed-out `aincr()` or `aadd()` twice; a configured `retry` is
  kept.
- Native views whose `optimize_queryset()` override does not call `super()` no
  longer fail on list and retrieve.
- Native paginators honour a `get_count()` override and a custom Django
  paginator `count` (capped or estimated counts) instead of always running
  `COUNT(*)`.
- A SIGINT or SIGTERM that reaches an `AsyncCommand` as its handler returns now
  raises `KeyboardInterrupt`/exits 143 instead of `RuntimeError: Event loop is
  closed` or being ignored.
- `aiodrf_inspect_serializers` reports views that choose their serializer in
  `aget_serializer_class()`.
- The codemod rewrites adrf's permission names (`AsyncBasePermission`, `AAND`,
  `AOR`, `ANOT`, operand holders) to aiodrf's and DRF's under their old names;
  adrf names and modules without a counterpart stay on adrf with a note instead
  of becoming failing imports.
- The codemod renames `self.`/`super().` references to adrf hooks and renamed
  actions in every method of a view, recognises adrf's `*args, **kwargs` action
  signature (as does `aiodrf.W006`), notes `super().acreate()`/`aupdate()` in
  serializers, and keeps comments and trailing commas of multi-line imports.
- adrf compatibility: `super().acreate()` and `super().aupdate()` in an adrf
  `ModelSerializer` subclass run DRF's write in a thread instead of raising
  `AttributeError`.
- `aiodrf_convert` marks explicit field validators, validators
  `ModelSerializer` derives from unique constraints and non-constraint
  `Annotated` metadata (`AfterValidator`, `Strict`, `msgspec.Meta(tz=...)`)
  instead of dropping them; renames `_`-prefixed fields for pydantic with an
  alias; writes `IntEnum` defaults as values; and accepts the blank string a
  blank-able `CharField` accepts.
- System checks tagged `urls` no longer crash in a project without
  `ROOT_URLCONF`.
- drf-spectacular: schemas of pydantic/msgspec serializers are valid OpenAPI 3.0
  (exclusive bounds, `const`, tuples) and components of parametrized generic
  models get valid names.
