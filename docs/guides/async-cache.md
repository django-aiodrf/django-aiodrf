# Async caching

Native backends and middleware are provided by the optional `aiodrf-async-cache`
package. Install its `redis` or `valkey` extra. Lifespan integration additionally
uses `aiodrf-asgi-lifespan`; neither package requires aiodrf.

An `aget()` method does not establish native network I/O. Django's default
async cache methods adapt synchronous operations to a worker thread. aiodrf
supports those backends unchanged and provides explicit native alternatives.

| Configuration | Async cache I/O | Standard Django cache middleware |
| --- | --- | --- |
| Django Redis / `django_redis.cache.RedisCache` | Thread-adapted synchronous commands | Supported through the synchronous API |
| `django_valkey.async_cache.cache.AsyncValkeyCache` | Native valkey-py coroutines | Not compatible directly; use the async adapter below |
| `aiodrf_async_cache.redis.AsyncRedisCache` | Native redis-py coroutines | Async-only; use the native adapter, or a separate sync alias for standard middleware |
| `aiodrf_async_cache.valkey.AsyncValkeyCache` | Native valkey-py coroutines | Async-only; awaited callbacks, Sentinel and Cluster with the same cache contract as the Redis adapter |

The native adapter does not modify Django middleware, replace django-redis
methods or turn sessions and DRF throttles into native consumers. Keep a
synchronous default cache for those consumers and a lifespan-owned native
instance for explicit async requests.

## Use Django's cache API directly

`CACHES` is a Django setting. The `BACKEND` import path selects the
implementation; `django.core.cache.cache` is a proxy for the `default` alias,
and `caches["name"]` selects another alias. No contrib adapter is needed to use
Django's built-in Redis backend:

```python
# settings.py; Django's RedisCache needs the redis package.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": "redis://127.0.0.1:6379/0",
        "KEY_PREFIX": "my-service",
        "TIMEOUT": 60,
        "OPTIONS": {"socket_connect_timeout": 2, "socket_timeout": 2},
    },
}
```

```python
from django.core.cache import cache, caches


def synchronous_summary():
    cache.set("summary", {"count": 42}, timeout=30)
    return caches["default"].get("summary")


async def asynchronous_summary():
    await cache.aset("summary", {"count": 42}, timeout=30)
    return await cache.aget("summary")
```

These functions use the same backend and key format. With Django's Redis
backend, the second function awaits thread-adapted synchronous I/O; it does not
select `redis.asyncio`. With `django_redis.cache.RedisCache`, the same distinction
applies. Keep either backend when synchronous cache middleware, existing plugins
or minimal application changes are the priority. Backend-specific options are
not interchangeable: django-redis uses options such as
`CONNECTION_POOL_KWARGS`, whereas Django's built-in Redis backend forwards driver
options directly. See [Django's cache API](https://docs.djangoproject.com/en/stable/topics/cache/#the-low-level-cache-api)
and [django-redis configuration](https://github.com/jazzband/django-redis#configure-as-cache-backend).

In an async view, do not call a synchronous network backend's `get()` or `set()`
directly. It can block the event loop even if Django does not raise
`SynchronousOnlyOperation`. Use its `a`-prefixed methods. Conversely, do not call
`asyncio.run()` inside a sync view to use a loop-owned native cache: use a sync
alias instead. Django adapting sync middleware under ASGI preserves its sync
execution model; it does not replace that middleware's cache client.

### Direct native backend access

The django-valkey backend and both aiodrf native backends also live in Django's
`CACHES` configuration. Native refers to their driver I/O, not a different key
API. The [configuration below](#backend-configuration) defines a `native` alias.
For a bounded async job, the direct Django construction API is sufficient:

```python
from django.core.cache import caches


async def refresh_summary():
    # Independent instance: this function owns it, not the request registry.
    native = caches.create_connection("native")
    try:
        await native.aset("summary", {"count": 42}, timeout=30)
        return await native.aget("summary")
    finally:
        await native.aclose()
```

For django-valkey, configure `CLOSE_CONNECTION=True` as below. `caches["native"]`
can also resolve the alias inside an async context, but Django's registry is
context-local, not a worker-wide pool owner. Do not use repeated registry lookup
as a substitute for ASGI resource management. For web requests, enter
`cache_lifespan("native")` once per worker lifespan and obtain that instance with
`get_lifespan_state()`. This contrib helper wraps the same direct construction
and cleanup shown above; it does not replace Django's cache registry.

Both native contrib backends expose only the async cache operations. Keeping a
separate sync alias is necessary for consumers that call `get()`/`set()`,
including Django's standard cache middleware and ordinary DRF throttles. A sync
and native alias may share server data only when key prefix, version and value
serialization match. Sharing data does not mean sharing connection pools.

## Backend configuration

Choose one backend for the `native` alias. Install `aiodrf-async-cache[django-valkey]`
for django-valkey or `aiodrf-async-cache[redis]` for redis-py. The latter does not
install django-redis; install that package separately if a synchronous alias
uses it.

### django-valkey

```python
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    },
    "native": {
        "BACKEND": "django_valkey.async_cache.cache.AsyncValkeyCache",
        "LOCATION": "valkey://127.0.0.1:6381/14",
        "KEY_PREFIX": "my-service",
        "TIMEOUT": 60,
        "OPTIONS": {
            "CONNECTION_FACTORY": "aiodrf_async_cache.django_valkey.LifespanConnectionFactory",
            "CONNECTION_POOL_CLASS": "valkey.asyncio.connection.BlockingConnectionPool",
            "CONNECTION_POOL_KWARGS": {"max_connections": 20, "timeout": 2},
            "SOCKET_CONNECT_TIMEOUT": 2,
            "SOCKET_TIMEOUT": 2,
            "CLOSE_CONNECTION": True,
            "IGNORE_EXCEPTIONS": False,
        },
    },
}
```

`LifespanConnectionFactory` keeps a pool per URL on its own instance. Repeated
factory calls and cache operations reuse it. Different factories never share
pools, even for identical URLs. This avoids django-valkey 0.4.1's process-global,
URL-only registry shared by its sync and async factories. The subclass does not
modify that registry or any signal.

The factory belongs to one event loop and lifespan; never share it across
threads, loops or worker processes. Pool construction is not a new network
connection per request: connections open lazily, subject to the pool limit.
`CLOSE_CONNECTION=True` is required for the vendor's `aclose()` to disconnect
them at shutdown.

### redis.asyncio

```python
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": "redis://127.0.0.1:6380/14",
        "KEY_PREFIX": "my-service",
    },
    "native": {
        "BACKEND": "aiodrf_async_cache.redis.AsyncRedisCache",
        "LOCATION": "redis://127.0.0.1:6380/14",
        "KEY_PREFIX": "my-service",
        "TIMEOUT": 60,
        "OPTIONS": {
            "socket_connect_timeout": 2,
            "socket_timeout": 2,
            "async_pool_class": "redis.asyncio.BlockingConnectionPool",
            "async_pool_kwargs": {"max_connections": 20, "timeout": 2},
        },
    },
}
```

`AsyncRedisCache` implements Django's `BaseCache` async interface. Keys, versions,
timeout normalization and default value serialization follow Django's Redis
backend. Standard synchronous consumers use the separate `default` alias above.
There is no implicit `async_to_sync` connection path. Use the native backend
through `cache_lifespan()`. Constructing it opens nothing, so Django's system
checks, which build every configured alias, can construct it outside an event
loop. The first event loop that uses it owns its pool; using or closing it from
another loop raises `RuntimeError`.

| Native option | Default | Meaning |
| --- | --- | --- |
| `async_pool_class` | `redis.asyncio.BlockingConnectionPool` | Class/import path; must derive from an async ConnectionPool |
| `async_pool_kwargs` | `{"max_connections": 20, "timeout": 2}` | Overrides native pool defaults; `timeout` only applies to the blocking pool |
| `serializer` | Django's `RedisSerializer()` | Class/import path or instance with sync or async `dumps`/`loads` |
| `callback_mode` | `"thread"` | Offload synchronous callbacks; `"inline"` explicitly accepts CPU/blocking work on the loop |
| `topology` | `"standalone"` | `"standalone"`, `"sentinel"` or `"cluster"` |
| `sentinels` | `None` | Sentinel discovery endpoints as `(host, port)` pairs |
| `sentinel_kwargs` | `{}` | Discovery-node credentials, TLS and socket options, separate from data-node options |
| `socket_connect_timeout`, `socket_timeout` | Driver defaults | Bound connection/read waits explicitly in deployments |
| `health_check_interval`, `retry`, `retry_on_error` | Driver/topology defaults | Optional native-driver liveness checks and retry policy; see [connection resilience](cache-resilience.md) |

Remaining options are passed to the native driver, not to django-redis's plugin
configuration. `async_pool_class` is available only in standalone mode. Sentinel
and Cluster use the driver's topology-aware pools with a default capacity of 20
(per node for Cluster); supply `async_pool_kwargs` to change capacity.
`decode_responses=True` is rejected because serialized values require bytes.
URL query parameters may override keyword arguments; avoid conflicting settings.

The core async API is supported: `aget`, `aset`, `aadd`, `atouch`,
`adelete`, `ahas_key`, `aget_many`, `aset_many`, `adelete_many`,
`aget_or_set`, `aincr`, `adecr`, version changes and `aclear`.
Batch writes use a pipeline. Integer increments check existence and update
atomically in Lua, preserving TTL; missing counters raise `ValueError`.
Redis ACLs must allow the commands used, including `EVAL` for counters.
Version changes and `aget_or_set` retain Django's multi-operation semantics,
not transaction or single-flight guarantees.

This adapter does not port django-redis's Herd, client-side sharding, compression,
exception suppression or custom-client plugins. Sentinel and Cluster are native
driver configurations described below, not django-redis plugin implementations.
Use the lowercase options above, not uppercase django-redis client options.
Default-format values and page responses are tested with django-redis in both
directions; custom formats require an explicit migration contract. TLS and Unix
sockets use redis-py's URL support, but deployment credentials and certificate
verification still need validation.

### Valkey with awaited callbacks and topology support

Use `aiodrf_async_cache.valkey.AsyncValkeyCache` with the same lowercase options as
`AsyncRedisCache`, a `valkey://` URL and, if specified, a
`valkey.asyncio.connection.BlockingConnectionPool`. This is an explicit alternative
backend using valkey-py, not a patch or subclass of django-valkey's client system.
Keep the vendor backend above when its additional hash, pattern, compression or
Herd commands are required. Those plugin APIs are not copied into the new backend.
Default pickle/integer values use Django's Redis-compatible format; do not share
keys with a vendor alias configured with another serializer or compressor.

### Sentinel

```python
CACHES["native"] = {
    "BACKEND": "aiodrf_async_cache.redis.AsyncRedisCache",
    "LOCATION": "cache-primary",  # Sentinel service name, not a URL.
    "KEY_PREFIX": "my-service",
    "OPTIONS": {
        "topology": "sentinel",
        "sentinels": [("sentinel-1", 26379), ("sentinel-2", 26379)],
        "sentinel_kwargs": {"socket_connect_timeout": 2, "socket_timeout": 2},
        "socket_connect_timeout": 2,
        "socket_timeout": 2,
        "db": 0,
        "async_pool_kwargs": {"max_connections": 20},
    },
}
```

The backend retains one primary client and closes both its pool and the Sentinel
discovery clients at shutdown. Reads use the primary; replica routing is not
enabled implicitly. Supply data-node authentication/TLS options separately from
`sentinel_kwargs`. Sentinel uses the driver's fail-fast pool, not a blocking pool;
capacity exhaustion raises without blocking the event loop. Failover can interrupt
requests. [Retry policy](cache-resilience.md) belongs to the driver/application, and an interrupted write
may already have reached the server. Blindly retrying increments is not safe.

### Cluster

```python
CACHES["native"] = {
    "BACKEND": "aiodrf_async_cache.redis.AsyncRedisCache",
    "LOCATION": "redis://cluster-seed:6379/0",
    "KEY_PREFIX": "my-service",
    "OPTIONS": {
        "topology": "cluster",
        "socket_connect_timeout": 2,
        "socket_timeout": 2,
        "async_pool_kwargs": {"max_connections": 20},
    },
}
```

Cluster supports database 0. The client discovers nodes from the seed; advertised
addresses must be reachable by the application. For multiple seeds, pass the
driver's `startup_nodes` objects in options. Native single-key counters remain
atomic. `aget_many`, `aset_many` and `adelete_many` pipeline individual commands
with `transaction=False`, so keys can occupy different hash slots. Each SET
carries its TTL; partial completion remains possible. No cross-slot transaction
or rollback is promised. `aclear()` sends FLUSHDB to all primary nodes. It clears the entire cluster,
including keys outside `KEY_PREFIX`; use a dedicated cache database.

Use raw `cache.async_client.pipeline(transaction=False)` for explicit Cluster
commands. Standalone and Sentinel pipelines may use transactions. Cluster capacity
is per node and does not implement a waiting queue; budget it across workers and
topology size rather than interpreting 20 as a process-wide connection limit.

## Lifespan and request access

Create one cache per server lifespan, outside Django's request-local registry:

```python
# myapp/lifecycle.py
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from aiodrf_async_cache.middleware import AsyncCache
from aiodrf_async_cache.lifespan import cache_lifespan


@asynccontextmanager
async def lifespan() -> AsyncGenerator[AsyncCache, None]:
    async with cache_lifespan("native") as cache:
        yield cache
```

```python
# settings.py
DJANGO_LIFESPAN = "myapp.lifecycle.lifespan"

# asgi.py
from aiodrf.asgi import get_asgi_application

application = get_asgi_application()
```

Use the concrete backend type when views need its full API. For Valkey, replace
`AsyncRedisCache` below with the vendor's `AsyncValkeyCache`:

```python
from aiodrf_asgi_lifespan.asgi import get_lifespan_state
from aiodrf_async_cache.redis import AsyncRedisCache
from aiodrf.response import Response
from aiodrf.views import APIView


class CachedSummary(APIView):
    async def get(self, request):
        cache = get_lifespan_state(request, AsyncRedisCache)
        value = await cache.aget("summary")
        if value is None:
            value = {"status": "ready"}
            await cache.aset("summary", value, timeout=30)
        return Response(value)
```

`AsyncCache` is the middleware's small typing protocol (`aget`, `aset`,
`aclose`), not the vendor's complete API. `cache_lifespan()` constructs the
alias without registering it in `caches` and awaits `aclose()` on normal or
exceptional exit. It requires `is_async=True`; third-party backends must honor
that contract and actually close their resources.

Do not use `caches["native"]` per request expecting worker-wide pool reuse.
Do not reuse a closed instance. The Redis adapter also rejects cross-loop
access. For composite resources, use `AsyncExitStack` to own the cache alongside
HTTP/search clients.

## Page-cache middleware

Replace the two cache middleware entries explicitly:

```python
MIDDLEWARE = [
    "aiodrf_async_cache.middleware.AsyncUpdateCacheMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "aiodrf_async_cache.middleware.AsyncFetchFromCacheMiddleware",
]
CACHE_MIDDLEWARE_SECONDS = 60
CACHE_MIDDLEWARE_KEY_PREFIX = "pages"
```

As in Django, update goes first and fetch last, preserving headers set by
intermediate middleware. The lifespan supplies the backend; the native adapter
does not use `CACHE_MIDDLEWARE_ALIAS` for request-time lookup. Other page-cache
settings retain their Django meaning. Do not install both the native and
standard cache middleware on the same path.

`AsyncCacheMiddleware` combines both phases for simple stacks. Prefer the
split form when locale, sessions or other middleware changes `Vary`. These
adapters require an async chain and a rendered response from Django. They are
not view decorators or a WSGI-native solution. Standard middleware and
`aiodrf.cache.cache_page` remain the synchronous-backend integration.

The adapter supplies request-local cache results to Django's `CacheMiddleware`,
awaits missing reads, then evaluates the policy again. It captures Django's
writes and awaits them in order. Only the policy repeats; views, rendering and
network writes are never replayed. Cache keys, HEAD fallback, `Age`,
language/timezone suffixes, `Vary`, status codes and security exclusions
therefore follow the installed Django version, without copying its policy or
monkeypatching it. Keep Django patched: this is not a backport of upstream
security changes. Policy evaluation remains synchronous CPU work.

For a composite lifespan value, subclass the desired middleware and override
`get_cache(self, request)` to return that resource's cache. The
[services example](../../examples/ecosystem-services/README.md) demonstrates
this. Never cache private/authenticated responses without an explicit
authorization, `Vary` and `Cache-Control` policy.

## Pool capacity and cancellation

`valkey.asyncio.BlockingConnectionPool` and
`redis.asyncio.BlockingConnectionPool` are async pools. Their name refers to
waiting for capacity: they suspend the waiting task using asyncio primitives,
not the event-loop thread. A finite `timeout` bounds that wait. Cancellation
releases a waiter; an in-flight cancelled write may already have reached the
server, so do not blindly retry non-idempotent commands.

For immediate overload errors, use `valkey.asyncio.ConnectionPool` or
`redis.asyncio.ConnectionPool` and supply only `max_connections` in the pool
kwargs. This changes failure policy, not whether network I/O is async. Never
substitute the similarly named synchronous pool classes.

Four workers with a native limit of 20 may open 80 native connections. Include
synchronous consumers and replicas separately. A larger pool is not necessarily
faster; measure queue pressure, timeouts and hit rate with application workloads.

## Raw operations and boundaries

The contrib Redis and Valkey backends expose `cache.async_client` for pipelines
and SCAN. The django-valkey vendor backend instead exposes
`await cache.client.get_client()`. Raw commands bypass serialization and key
transformation; use `await cache.amake_key()` with the contrib backends, including
when `KEY_FUNCTION` is async. The vendor backend's key API is synchronous.
Never mix arbitrary raw bytes and pickled objects under one key. Valkey's
`aiter_keys()` is a vendor extension, not an API added to the Redis adapter.

- `aclear()` clears the selected database, not just `KEY_PREFIX`. Never use
  it on a shared service. Tests delete only their own keys, without flushing.
- Pickle serialization requires trusted cache writers and an authenticated,
  private service. An untrusted writer can turn reads into code execution;
  key prefixes are not security boundaries.
- Vendor django-valkey's serialization/key callbacks remain synchronous. The
  contrib backends await async callbacks and offload synchronous callbacks by
  default, as described below; this does not change the vendor's API.
- Join/cancel request tasks before closing pools. Cleanup cannot undo accepted
  writes or provide exactly-once delivery.
- Failures propagate. The contrib backends never silently treat server failure
  as a miss. The django-valkey vendor backend's exception suppression must be an
  explicit application policy.
- Importing django-valkey 0.4.1 replaces Django's request-finished cache-close
  receiver. This vendor-owned change is in the
  [adaptation inventory](../reference/runtime-adaptations.md); no aiodrf
  middleware or factory installs it. Vendor tests run in a separate process.

## Awaited callbacks and typed values

Both contrib backends accept sync or async `KEY_FUNCTION`, serializer `dumps` and
`loads`, and the callable default of `aget_or_set`. Synchronous decorators around
async functions run their synchronous prefix in a thread; returned awaitables run
on the owning loop. Exceptions and cancellation propagate without publishing a
partially encoded value. A failed or cancelled network write can still have been
accepted by the server.

```python
class PayloadCodec:
    async def dumps(self, value):
        return await application_encoder.encode(value)

    async def loads(self, value):
        return await application_encoder.decode(value)


async def make_key(key, prefix, version):
    return f"{prefix}:{version}:{key}"


CACHES["native"]["KEY_FUNCTION"] = make_key
CACHES["native"]["OPTIONS"]["serializer"] = PayloadCodec
```

`application_encoder` above is an application-owned service, not an aiodrf API.
Key functions must be deterministic for reads, writes and deletes. If tenant
context influences a key, capture it explicitly for background operations too.
`aget_or_set` evaluates its factory only on a miss, but competing misses can each
run a factory. It is not a distributed lock or single-flight facility.

`callback_mode="thread"` is the default. Synchronous value codecs and custom key,
validation and factory callbacks run in Django's thread-sensitive worker. The
standard Django key formatter and key validation stay inline. Async callbacks
are responsible for not blocking. `callback_mode="inline"` is an explicit choice
for measured, bounded CPU-only callbacks: it removes hops, not CPU work. It must
not be enabled for callbacks that query the synchronous ORM or perform network I/O.
Callback objects are reused within the lifespan and must tolerate concurrent use.

Optional typed codecs import their dependencies only on construction:

```python
from datetime import datetime
from pydantic import BaseModel
from fastdrf.codecs import PydanticCodec

from aiodrf_async_cache.codecs import MsgspecCodec


class Summary(BaseModel):
    count: int
    generated_at: datetime


# Pydantic validation and JSON; a TypeAdapter instance is also accepted.
CACHES["native"]["OPTIONS"]["serializer"] = PydanticCodec(Summary)

# Alternative: MessagePack; pass a msgspec Struct type for typed decoding.
# CACHES["native"]["OPTIONS"]["serializer"] = MsgspecCodec(SummaryStruct)
```

Install the `pydantic` or `msgspec` extra in addition to the chosen cache extra.
For a type expression (`PydanticCodec(float | None)`), the codec configures
Pydantic's JSON so that every value the type accepts comes back: non-finite
floats as `NaN`/`Infinity` rather than `null`, bytes as base64 rather than
UTF-8 only. A model, dataclass or TypedDict uses its own `model_config`, and a
given `TypeAdapter` is used as it is; set `ser_json_inf_nan` and the bytes modes
there if the values can hold them.
Pydantic fields/validators/serializers retain Pydantic semantics; msgspec retains
its Struct and `enc_hook`/`dec_hook` contracts. Those internal vendor hooks remain
synchronous: the cache offloads the complete codec call, rather than pretending a
C encoder can await field hooks. Async preprocessing belongs in an outer async
`dumps`/`loads` callback. The typed codecs do not pickle objects. Untyped JSON and MessagePack can
normalize tuples, dates or dictionary keys; specify a schema when reconstruction
matters. MessagePack and JSON are distinct wire formats. MessagePack integers
are 64-bit: `MsgspecCodec` raises `OverflowError` for a larger `int`, which
`PydanticCodec` stores; store such values as `str` or use `PydanticCodec`.

Do not replace the codec of a populated alias without changing its prefix or
version. Typed codecs are for application data, not arbitrary `HttpResponse`
objects; page-cache middleware should use a separate default-format alias.
Counters require raw integer wire compatibility. `aincr`/`adecr` reject the
provided typed codecs; a custom codec may declare
`supports_integer_operations=True` only if it preserves Redis/Valkey integer
encoding. Custom hooks and compressed payloads are not automatically compatible.

## Tested behaviour

The async cache middleware applies the same caching policy as Django's. With
real Redis and Valkey servers, the native backends are tested for their
operations, compatibility with synchronous cache clients, real ASGI requests,
pool reuse, cancellation, event-loop ownership and shutdown; callback contexts,
typed codecs and the choice of Cluster commands are tested as well. Sentinel
discovery and failover and cross-slot Cluster operations are tested with both
clients. These tests do not establish high availability, TLS, latency or memory
behaviour in production.

The service profiles also exercise pool reconnection, bounded opt-in retries,
reply loss after an accepted write, retry exhaustion and cancellation during
backoff. See [connection resilience](cache-resilience.md#verification) for the
fault model and its limits.

References: [Django caching](https://docs.djangoproject.com/en/stable/topics/cache/),
[django-valkey async configuration](https://django-valkey.readthedocs.io/en/latest/async/configurations/),
[redis-py async lifecycle](https://redis.readthedocs.io/en/stable/examples/asyncio_examples.html),
[django-redis](https://github.com/jazzband/django-redis).
Other optional dependencies are listed in the
[contrib documentation reference](../reference/contrib-dependencies.md).
