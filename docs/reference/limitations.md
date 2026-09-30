# Limitations

aiodrf adds asynchronous execution to DRF. It does not replace Django's ORM,
middleware or transaction handling, and it cannot make blocking code
non-blocking. This page lists the boundaries to keep in mind. Optional features
are disabled until you enable them through their setting or class option.
"Compatible with DRF" means that aiodrf behaves as DRF does for the supported
versions; it does not cover every custom field or third-party extension.

## Async views and the ORM

Django's awaitable ORM methods, such as `aget()` and `asave()`, run the database
driver synchronously in a worker thread. They keep the event loop free while the
query runs, but the driver itself is not asynchronous, and the number of worker
threads and database connections still limits concurrency. Querysets remain
lazy: reading a deferred field or an unloaded relation issues a query, and in
async code that query raises `SynchronousOnlyOperation`. `async for` and
`aiterator()` buffer and prefetch differently; choose based on your Django
version and workload.

- Load relations explicitly with `select_related()` and `prefetch_related()`,
  and paginate large results.
- Do not set `DJANGO_ALLOW_ASYNC_UNSAFE`.
- Under ASGI, keep `CONN_MAX_AGE = 0` and use your database backend's connection
  pool if you need one. Awaiting an ORM call does not return an open connection
  to a pool.
- Do not close another thread's database connection from the event loop.

See [Django's async documentation](https://docs.djangoproject.com/en/6.0/topics/async/)
and [transactions in async views](../guides/async-transactions.md).

## Transactions and cancellation

`transaction.atomic()` is a synchronous context manager, and Django's
`ATOMIC_REQUESTS` cannot wrap an async view. Put each transactional operation in
a single synchronous function and call it with `sync_to_async`; a lock, a read
and a write spread over separate `await`s do not share a transaction.

`AIODRF["ATOMIC_SAVE"]` wraps aiodrf's default serializer save, including
many-to-many writes, in a transaction. It does not cover the whole request,
your own `create()` or `update()` overrides, or calls to other services.

Cancelling a request does not stop a worker thread that is already running, and
it does not undo a committed write. `on_commit` callbacks run after the commit,
so a failure to publish a message there cannot roll the commit back; use an
outbox table if publication must be reliable.

## Middleware and synchronous hooks

Under ASGI, Django runs synchronous-only middleware in a worker thread, which
adds a thread switch per middleware. Do not reorder security, session or
authentication middleware to avoid these switches: their order matters more.

Synchronous authentication, permission, throttle, filter, validation and
representation hooks run in a worker thread unless aiodrf knows they perform no
I/O. Declaring a hook safe (`async_safe`, `AIODRF["PURE_POLICIES"]`) is a promise
you make; it does not make I/O asynchronous. Test custom descriptors, lazy users
and third-party hooks under ASGI.

`aiodrf.test.count_hops()` counts aiodrf's own switches to a worker thread, not
Django's or asgiref's. It records one entry per switch, so keep the measured
block short; tasks started inside the block are counted only while the block is
open.

After an aiodrf `Response` is closed, it releases the references between the
request objects: `response.renderer_context["response"]`, the view's `response`
attribute and Django's `head` alias, and the view and request in the request's
`parser_context`. Code that reads them after the response was sent (Django's
test client closes the response before returning it) finds them missing;
`response.data` and the view and request in `renderer_context` remain. A
serializer that returned the response's `ReturnList` or `ReturnDict` builds its
`fields` again if they are read, and the list child's `parent` becomes a weak
proxy of the list (`==` still holds, `is` does not). With asgiref older than
3.9, a caught exception keeps the request's objects alive until Python's cycle
collector runs.

## CPU-bound work and free-threaded Python

An async function does not make JSON encoding, password hashing, compression or
validation non-blocking. Large responses and many concurrent item tasks can
delay the event loop and raise tail latency, even when the total work is lower.
See [CPU-bound work](../guides/cpu-work.md).

The free-threaded build of Python does not make every extension thread-safe;
some binary modules re-enable the GIL. Check that your interpreter stays in the
mode you expect.

JSON response data is inspected before it is encoded on the event loop. This
happens for every response, including cached and compiled output, because the
data can be modified after it was produced. Instances of the
built-in JSON renderers with attributes of their own are rendered in a worker
thread, because such an attribute can replace a method or the encoder. See
[request lifecycle and rendering](../guides/performance.md#request-handling-and-rendering).

## Compiled serializers

The msgspec, Pydantic and python compilers support a documented subset of DRF
serializers. Custom `to_representation`, method fields, JSON fields
in strict parity, dotted sources through nullable foreign keys, unusual model
descriptors and custom list serializers prevent
compilation; such serializers fall back to DRF, or raise with
`SERIALIZER_BACKEND_FALLBACK = "error"`. Compiled plans are cached per set of
fields, so serializers whose fields change per request benefit less. Do not
change serializer or model declarations after a class has been used.

The caches are bounded by number of entries, not by memory size, and an entry
can keep objects referenced by the serializer's declarations alive until it is
evicted. Do not capture request objects in field declarations or registered
callbacks. See [state ownership](../architecture/state-ownership.md).

`"fast"` parity accepts documented differences from DRF's output, notably for
decimals; it is not simply a faster strict mode. Compare Python values and the
rendered bytes, particularly for dates, time zones, decimals, nulls and omitted
fields. aiodrf does not replace DRF's synchronous `.data`; use aiodrf's
operations or an integration you enable explicitly.

`MsgspecSerializer` and `PydanticSerializer` validate with their library's
rules, which can coerce values and report errors differently from DRF, even for
matching annotations. The schema conversion command produces source code to
review; it does not transfer custom validation or model constraints.

These serializers accept Pydantic `BaseModel` and `RootModel` classes and
msgspec `Struct` classes, not arbitrary `TypeAdapter` annotations; wrap a root
value in a `RootModel`.

- Pydantic receives DRF's serializer context. msgspec custom types need
  `Meta.dec_hook` and `Meta.enc_hook`, plus `Meta.schema_hook` for OpenAPI.
- Hooks are synchronous and may run more than once during custom output
  projection. Do not declare async Pydantic or msgspec hooks: the libraries do
  not await them. Put async work in serializer or view hooks instead.
- Unsupported types raise an error; they do not fall back to DRF.
- Root and array outputs are supported, but a `PATCH` contract for them must be
  defined explicitly.
- Parsing and rendering remain separate DRF steps; there is no combined
  decode-validate-encode pipeline.
- DRF still handles missing input and `null` for the whole serializer: a
  nullable root annotation does not enable `allow_null`.
- Automatic model writes do not save nested relations or many-to-many fields.

See [native validation and serialization hooks](../guides/msgspec-pydantic.md#native-validation-and-serialization-hooks).

## Compiled input validation

Compiled input validation accepts only input that is already in the form DRF
would produce after validation. Anything else, including values that need
coercion, custom validators, relations and hooks, is validated by DRF. It does
not format errors differently and does not replace database uniqueness checks.

Typed lists, dictionaries and HStore values in canonical form are recognized,
including nested ones. A child field with a custom validator, a hook or an
unsupported type sends the whole input to DRF. Times with a UTC offset,
datetime time-zone conversion, decimal precision and rounding, and file and
relation validation always use DRF's rules. The
[type coverage table](serializer-type-coverage.md) lists each case.

## Field cache and copy plans

The field cache requires static declarations. Custom construction, dynamic
field-building hooks, callable model choices, custom model fields and
`Meta.depth` make a model serializer build its fields per instance, as in DRF.
Mutable state is never shared between the fields of different requests.

`deepcopy` copies fields as DRF does. `clone` skips constructor replay for a
small set of built-in scalar fields. `compiled` also plans the copying of nested
constructor arguments, containers and serializers; it is a precomputed Python
plan, not native code. Querysets, custom copy hooks, positional constructor
arguments and unsupported objects are copied with their own protocol. Nested
DRF serializers build their fields as usual; nested aiodrf serializers inherit
the selected mode unless their `Meta` sets another one.

A serializer that changes its class declarations at run time must not use the
cache. Serializer instances are never safe to share between concurrent
requests. See [selective serializer optimization](../guides/serializer-optimization.md).

## Relation batching and prefetching

Batched lookups apply only to primary-key relation input. Custom querysets,
managers or fields, duplicate keys and invalid input are validated by DRF.
Fewer queries do not replace object-level authorization. Automatic prefetching
cannot detect relations that only your own code reads; declare those lookups
explicitly. Batching data from another service requires that service to offer a
batch API. Concurrent list serialization is experimental and can increase the
latency of other requests.

## Static files

The optional WhiteNoise adapter supports async middleware, but WhiteNoise reads
files synchronously, so Django buffers those responses under ASGI and logs a
warning. ServeStatic is an ASGI-capable alternative; a reverse proxy or CDN is
usually the better choice in production. Never serve user uploads from the
static files location. See [static files](../guides/static-files.md).

## Streaming and lifespan

Once a streaming response has sent its headers, an error can no longer be
turned into a JSON error response. Proxies may buffer or cut long streams, and
Server-Sent Events do not replay missed events on their own. HTTPX's in-process
transport does not reproduce socket backpressure or disconnects, so test
streaming behind your real server and proxy. Acquire resources inside the
producer, release them in `finally`, and let cancellation propagate.

The lifespan context runs only when the server supports the ASGI lifespan
protocol and the application is created with aiodrf's `get_asgi_application()`.
The factory is a zero-argument function returning an async context manager, not
a coroutine function. Resources belong to the event loop that created them; do
not share clients between workers or event loops. Django's WSGI handler and
management commands do not enter the web application's lifespan.

## Caching and throttling

Django's async cache API can run a synchronous backend in a worker thread.
`LocMemCache` is local to one process, and `DummyCache` stores nothing.
Atomicity, eviction and consistency across processes depend on the backend.
Fixed-window throttling needs atomic cache operations and is not a strict
global quota when the cache fails or evicts keys. Do not cache personalized
pages without the right cache keys, `Vary` headers and `Cache-Control` policy.

## Third-party packages

Compatibility is tested for specific package versions and scenarios, listed in
the [ecosystem guide](../guides/ecosystem.md). Third-party middleware and
instrumentation can add their own queries, threads, callbacks or patches.

The options of the [tuned profile](../guides/tuned-profile.md) keep third-party
packages working, with these exceptions:

- With `SERIALIZER_BACKEND_FALLBACK = "error"`, serializers that cannot be
  compiled raise `ImproperlyConfigured`. This affects django-money
  (`MoneyField`), django-phonenumber-field, django-taggit
  (`TagListSerializerField`), django-polymorphic, drf-flex-fields and
  django-restql (which define their own `to_representation`),
  django-rest-framework-json-api (`ResourceRelatedField`),
  djangorestframework-dataclasses, the image fields of drf-extra-fields,
  django-pydantic-field (`SchemaField`) and the embedded models and arrays of
  django-mongodb-backend, as well as `*` sources and the other fields
  listed under [compiled serializers](#compiled-serializers). Set `Meta.serializer_backend_fallback = "drf"` on those
  serializers, or keep the default of `"drf"`.
- With `REPRESENTATION_MODE = "inline"`, anything that performs I/O while
  representing raises `SynchronousOnlyOperation`: S3 file URLs from
  django-storages (refreshing credentials is a network call), relations the
  view's queryset does not load, properties or method fields that query, and
  the many-to-many relations of an object just created by a serializer with
  async fields.
- `REQUEST_THREADS` keeps thread-local state between requests, as a threaded
  WSGI server does.

## Authentication, schemas and integrations

- The quick check for missing credentials does not replace the token validation
  of the authentication package.
- Session authentication still requires CSRF protection.
- django-guardian's list filtering and object permission checks are separate;
  enabling one does not enable the other.
- Schema generation cannot describe output that is built dynamically. Support
  for the HTTP QUERY method in Django and OpenAPI tools depends on their
  versions; the schema exclusion hook may still be needed. Streaming annotations
  describe the items, not errors raised while generating them.

**Native PostgreSQL backend.** It relies on django-async-backend, which supports
a subset of the ORM and instruments models in its own way; it does not support
every Django query and transaction pattern.

**MongoDB and Elasticsearch.** The Django MongoDB backend runs a synchronous
driver in worker threads and provides its own transaction adapter; transactions
require a replica set. `AsyncMongoClient` and Elasticsearch's `AsyncSearch`
perform native async I/O but bypass model hooks, queryset scoping and the
automatic synchronization between models and search indexes. See
[NoSQL databases](../guides/async-nosql.md).

**Native async caches.** django-valkey's native async backend is used by async
code only; it does not replace the cache used by synchronous middleware or DRF
throttles. The opt-in async cache middleware keeps Django's page-cache policy.
aiodrf's redis.asyncio and Valkey backends support standalone, Sentinel and
Cluster deployments, but not django-redis's Herd, compressor or client plugins.

- Key, codec and default callbacks may be async; synchronous callbacks run in a
  worker thread unless you allow them to run inline. Encoding is CPU work, not
  I/O.
- Typed codecs cannot serialize cached pages and do not support server-side
  integer counters.
- Cluster batches are not atomic across slots, and clearing the whole cluster is
  not supported.
- Synchronous middleware and throttles need a separate synchronous cache alias.
- Connection pools belong to a lifespan and its event loop.
- The django-valkey package replaces Django's cache-close signal receiver.

See [native async cache](../guides/async-cache.md).

**OpenSearch.** OpenSearch uses its own client and Django integration. aiodrf's
document writer does not replay related-model signals, management commands or
the package's update overrides. Preparing documents from models runs in a worker
thread; the HTTP requests are asynchronous. Building a query does not make the
synchronous `Search.execute()` awaitable. OpenSearch and Elasticsearch clients
and servers cannot be mixed. See [OpenSearch](../guides/async-nosql.md#opensearch).

**Tasks.** Django Tasks and django-tasks define a task API; they do not include
a production worker or a durable broker.

**Compatibility layers.** The ADRF import aliases and the experimental
middleware scheduling are migration aids, not a recommended architecture.

## Observability and deployment

Telemetry exporters can block, fail or expose sensitive data; sampling,
redaction, buffering and shutdown are the application's responsibility. Some
telemetry packages do not close spans correctly when a request is cancelled.
Local S3 and message broker substitutes cannot confirm IAM policies, credential
rotation, multipart upload cleanup or delivery during a production failover;
test these in your target environment.

aiodrf is alpha software: its public behaviour can change between minor
releases, as described in the [versioning policy](../guides/releasing.md).
