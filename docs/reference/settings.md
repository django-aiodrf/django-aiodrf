# Settings reference

Optional network integrations use Django's `CACHES` or their vendor settings,
not additional `AIODRF` keys. Native cache callback, codec, Sentinel and Cluster
options are listed with defaults in the [async cache guide](../guides/async-cache.md#backend-configuration).
OpenSearch connection and document settings are covered by the
[NoSQL guide](../guides/async-nosql.md#opensearch).

Configure aiodrf with the `AIODRF` dictionary in Django settings. Omitted keys
use the defaults below. Authentication, permissions, pagination, parsers,
renderers and exception handlers remain in `REST_FRAMEWORK`; databases,
middleware, storage and caches remain Django settings.

Run `python manage.py check` after changes. The settings object validates values
when accessed and Django system checks report unsupported or unknown keys.
Configure process-wide choices before starting workers. `override_settings()`
is supported in tests and invalidates the relevant caches; it is not a
request-local configuration mechanism.

## Default configuration

Omitting `AIODRF` is equivalent to the following configuration. Tests compare
this dictionary and the settings summary with the runtime defaults.

```python
AIODRF = {
    "LIFESPAN": None,  # or "project.lifecycle.lifespan"
    "UNSAFE_SYNC_MIDDLEWARE": False,  # only explicit aiodrf.unsafe.middleware paths
    "VALIDATION_UNKNOWN": "thread",  # or "inline": the project asserts it never blocks
    "REPRESENTATION_MODE": "thread",  # or "inline": instances are known to be loaded
    "ATOMIC_SAVE": True,  # the default save runs in transaction.atomic()
    "FETCH_MODE": None,  # Django 6.1: "peers" or "raise"
    "SERIALIZER_BACKEND": "drf",  # "msgspec", "pydantic" or "python"
    "SERIALIZER_BACKEND_PARITY": "strict",  # "fast" accepts the documented differences
    "SERIALIZER_BACKEND_FALLBACK": "drf",  # "error": raise when a serializer cannot be compiled
    "ALLOWED_SERIALIZER_BACKENDS": ["drf", "msgspec", "pydantic"],  # what views may use
    "PURE_POLICIES": [],  # classes to treat as @async_safe
    "INLINE_RENDERERS": [],  # renderers that are pure CPU work
    "CACHE_SERIALIZER_FIELDS": False,  # build a ModelSerializer's fields once per class
    "FIELD_COPY_MODE": "deepcopy",  # opt-in "clone" or recursive "compiled" plans
    "BATCH_RELATED_LOOKUPS": False,  # one query for PrimaryKeyRelatedField(many=True)
    "ADRF_COMPAT": False,  # serve an adrf project's imports from aiodrf
    "MONKEYPATCHES": [],  # opt-in patches of DRF classes, by name
    "REQUEST_THREADS": None,  # keep request threads (aiodrf.asgi), up to N idle
}
```

## Settings summary

| Setting                         | Default                            | Accepted values / scope                                                      |
| ------------------------------- | ---------------------------------- | ---------------------------------------------------------------------------- |
| `LIFESPAN`                    | `None`                           | `None`, context-manager factory or dotted path                             |
| `UNSAFE_SYNC_MIDDLEWARE`      | `False`                          | Boolean; explicitly selected experimental middleware only                    |
| `VALIDATION_UNKNOWN`          | `"thread"`                       | `"thread"`, `"inline"`                                                   |
| `REPRESENTATION_MODE`         | `"thread"`                       | `"thread"`, `"inline"`                                                   |
| `ATOMIC_SAVE`                 | `True`                           | Boolean; aiodrf-owned synchronous save unit                                  |
| `FETCH_MODE`                  | `None`                           | `None`, `"peers"`, `"raise"`; Django 6.1+                              |
| `SERIALIZER_BACKEND`          | `"drf"`                          | `"drf"`, `"msgspec"`, `"pydantic"`, `"python"`                         |
| `SERIALIZER_BACKEND_PARITY`   | `"strict"`                       | `"strict"`, `"fast"`                                                     |
| `SERIALIZER_BACKEND_FALLBACK` | `"drf"`                          | `"drf"`, `"error"`                                                       |
| `ALLOWED_SERIALIZER_BACKENDS` | `["drf", "msgspec", "pydantic"]` | Nonempty list/tuple of backend names                                         |
| `PURE_POLICIES`               | `[]`                             | List/tuple of classes or dotted paths; application nonblocking guarantee     |
| `INLINE_RENDERERS`            | `[]`                             | List/tuple of renderer classes or dotted paths                               |
| `CACHE_SERIALIZER_FIELDS`     | `False`                          | Boolean; static field-template cache                                         |
| `FIELD_COPY_MODE`             | `"deepcopy"`                     | `"deepcopy"`, `"clone"`, `"compiled"`; requires field caching          |
| `BATCH_RELATED_LOOKUPS`       | `False`                          | Boolean; eligible DRF primary-key list validation                            |
| `ADRF_COMPAT`                 | `False`                          | Boolean; explicit process-wide migration shim                                |
| `MONKEYPATCHES`               | `[]`                             | List of patch names (`aiodrf.contrib.monkeypatches.PATCHES`); process-wide |
| `REQUEST_THREADS`             | `None`                           | `None` or a positive integer; `aiodrf.asgi.get_asgi_application()`       |

Field-copy and relation-batching implementations live in `aiodrf.contrib.builtin`.
Serializer `Meta.cache_fields` / `field_copy_mode`, and view
`serializer_field_cache` / `serializer_field_copy_mode`, select the policy for
individual endpoints. See [selection precedence](../guides/serializer-optimization.md).

## Resource lifecycle

### LIFESPAN

Default: `None`. Values: `None`, a callable returning an async context manager,
or its dotted import path. Used only by `aiodrf.asgi.get_asgi_application()`.
An `async def` function or undecorated async generator is not a context-manager
factory; use `@contextlib.asynccontextmanager`. Resources belong to the event
loop entering the context. See [lifespan](../guides/lifespan.md).

## Synchronous application code

### VALIDATION_UNKNOWN

Default: `"thread"`. Values: `"thread"`, `"inline"`. Controls unclassified
synchronous validators and validation hooks. Inline asserts that the code
does not perform blocking I/O. It is never an optimistic attempt followed by
a retry in a worker. Keep the default for database-backed validators.

### REPRESENTATION_MODE

Default: `"thread"`. Values: `"thread"`, `"inline"`. Controls unclassified
representation requested through async serializer operations. In thread mode,
model instances whose serializer reads only what they have loaded (columns,
`select_related` and `prefetch_related` caches) are represented on the loop. Inline requires
loaded objects and nonblocking application hooks. Lazy relations can query;
explicit `select_related`/`prefetch_related` and regression tests are required.

### ATOMIC_SAVE

Default: `True`. Values: `True`, `False`. Wraps the synchronous save unit owned
by aiodrf, including default many-to-many writes. It does not wrap arbitrary
custom saves, the whole request or external I/O. It is not `ATOMIC_REQUESTS`
and does not make `transaction.atomic()` an async context manager.

### PURE_POLICIES

Default: `[]`. Values: a list/tuple of policy classes or dotted class paths.
Declares selected authentication, permission, throttle and filter code
nonblocking, equivalent to the documented `async_safe` extension contract.
Do not list a package wholesale or use this to bypass Django async safety.

### INLINE_RENDERERS

Default: `[]`. Values: a list/tuple of renderer classes or dotted class paths.
Declares additional renderers nonblocking. CPU encoding still occupies the
event loop; measure large responses and concurrent request latency.

## Serializer backends

### SERIALIZER_BACKEND

Default: `"drf"`. Values: `"drf"`, `"msgspec"`, `"pydantic"`, `"python"`.
Selects the backend for eligible compiled operations. Install the
corresponding extra for msgspec or pydantic; `"python"` needs no package and
compiles output only (DRF validates input).
`Meta.serializer_backend` overrides the selection per serializer. Ordinary
synchronous DRF `.data` does not implicitly activate the main package compiler.
See [serializer backends](../guides/msgspec-pydantic.md).

### SERIALIZER_BACKEND_PARITY

Default: `"strict"`. Values: `"strict"`, `"fast"`. Strict compilation requires
the documented DRF-equivalent output contract. Fast accepts documented backend
differences, including relevant decimal representation. Do not enable fast
globally without response-contract tests.

### SERIALIZER_BACKEND_FALLBACK

Default: `"drf"`. Values: `"drf"`, `"error"`. Controls unsupported compiled
output: fall back to DRF or raise `ImproperlyConfigured` with the reason. It
concerns serializers that cannot be compiled; an instance that a compiled
serializer cannot read (an unsaved instance's related objects, a deleted
related row, a missing annotation) is represented by DRF either way.
`Meta.serializer_backend_fallback` overrides this per serializer. Input
recognition that declines still uses DRF validation. Error mode is useful in
tests that must verify compiler eligibility.

### ALLOWED_SERIALIZER_BACKENDS

Default: `["drf", "msgspec", "pydantic"]`. Values: a nonempty list/tuple of
these names. Limits the serializer kinds accepted by views, including explicit
msgspec Struct and Pydantic model declarations. This does not install packages
or choose the active compiler.

## Query and field construction

### FETCH_MODE

Default: `None`. Values: `None`, `"peers"`, `"raise"`. Applies Django's
corresponding fetch mode to generic-view querysets on supported Django 6.1
versions. `raise` exposes implicit field loading; `peers` uses Django's peer
fetching. Neither replaces authorization, pagination or explicit query design.
Older supported Django versions cannot enforce this setting. See the
[fetch-mode guide](../guides/fetch-modes.md) for execution boundaries and per-view use.

### CACHE_SERIALIZER_FIELDS

Default: `False`. Values: `True`, `False`. Builds static aiodrf ModelSerializer
field templates once per class and returns independent fields per instance.
Custom field-building hooks, dynamic declarations and unsupported model-field
behavior retain per-instance construction. Do not mutate model or Meta
declarations after activation.

### FIELD_COPY_MODE

Default: `"deepcopy"`. Values: `"deepcopy"`, `"clone"`, `"compiled"`. Applies only when
field caching is enabled. Clone uses an opt-in copying plan for exact supported
scalar DRF fields; custom fields, relations, nested serializers and unsupported
arguments retain DRF deepcopy. Generated validators and their lazy messages
remain independent. Compiled mode also prepares recursive plans for containers
and nested serializers, retaining their constructor and custom-copy protocols.
It supports plain serializers as well as eligible model serializers. Select
`Meta.cache_fields` / `Meta.field_copy_mode` or the view's
`serializer_field_cache` / `serializer_field_copy_mode` for narrower activation;
serializer options take precedence. No DRF method is replaced. See
[selective optimization](../guides/serializer-optimization.md).

### BATCH_RELATED_LOOKUPS

Default: `False`. Values: `True`, `False`. Batches eligible
`PrimaryKeyRelatedField(many=True)` input reads into a `pk__in` lookup while
preserving DRF's validation errors. Custom querysets and unsupported relation
behavior retain normal validation. It does not batch writes or replace
database constraints.

## Explicit compatibility experiments

### ADRF_COMPAT

Default: `False`. Values: `True`, `False`. Explicit import compatibility
for ADRF applications. Read when the aiodrf app starts; ADRF must not also be
installed. It installs module aliases and is therefore an explicit exception
to the normal no-patching integration policy. Prefer the reviewed migration
tool over retaining the shim indefinitely. See [ADRF migration](../guides/migration-from-adrf.md).

### MONKEYPATCHES

Default: `[]`. Values: a list of patch names. Patches DRF for the whole
process, applied when the aiodrf app starts. Each is an explicit exception to
the normal no-patching policy; `aiodrf.contrib.monkeypatches.revert()` undoes
them.

| Name                         | What DRF does after it                                                                                                                                                                    |
| ---------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `"weak_list_children"`     | `ListSerializer` binds its child weakly, as aiodrf's does ([memory](../guides/performance.md#memory-and-garbage-collection))                                                             |
| `"release_drf_responses"`  | a closed DRF`Response` releases its request objects' cycles, as aiodrf's does                                                                                                           |
| `"cache_model_field_info"` | `model_meta.get_field_info(model)`, read to build every `ModelSerializer`'s fields, is computed once per model and again when Django recomputes the model's field caches              |
| `"keep_json_encoders"`     | `JSONRenderer.render` keeps the encoder it builds for every call, one per configuration; indented output, a subclass's `get_indent` or attributes set on the renderer keep DRF's code |

### REQUEST_THREADS

Default: `None`. Values: `None`, a positive integer. Read by
`aiodrf.asgi.get_asgi_application()`. Django's ASGI handler runs each
request's synchronous code (its signals, ORM calls, `sync_to_async`) in a
thread it starts for that request and joins, in another thread, after it: two
thread starts per request. With a number, the application keeps those threads
for later requests instead, up to that many idle ones. The rule stays Django's:
one request at a time in a thread, all of its synchronous code in that thread,
and a thread whose code is still running (an aborted request) is not lent
until it finished. Thread-local state outlives a request as in a threaded WSGI
server, database connections included: `request_started`/`request_finished`
still close them unless `CONN_MAX_AGE` keeps them, and then each kept thread
holds one per database. Measured at 16 concurrent requests: 25 % less CPU per
request on the cheapest endpoints ([performance](../guides/performance.md)).

### UNSAFE_SYNC_MIDDLEWARE

Default: `False`. Values: `True`, `False`. Affects only explicitly installed
`aiodrf.unsafe.middleware` subclasses, not Django's original classes. Requires
asgiref 3.12.1 or later. A worker can remain occupied while the view awaits;
measure concurrency before enabling. Configure before constructing handlers
and restart workers. See [middleware experiment](../guides/unsafe-middleware.md).
