# Cache connection resilience

Redis and Valkey native clients already reconnect disconnected sockets. The
aiodrf backends retain the client and pool; they do not recreate a pool after
each error. No additional reconnect wrapper, background monitor or monkeypatch
is needed. Configure the driver through Django's `CACHES` setting as described
in [async caching](async-cache.md).

## Reconnection and command retries

These are different operations:

| Mechanism | Behavior | Limitation |
| --- | --- | --- |
| Lazy reconnection | Opens a socket when a later command borrows a disconnected connection | Does not prove whether an earlier write succeeded |
| `health_check_interval` | Checks a connection before use after the driver's idle interval | Not a background heartbeat; adds a round trip when due |
| `retry` and `retry_on_error` | Replays a failed command with a bounded native-driver backoff | A lost reply can cause a write to execute twice |
| Sentinel | Discovers a new primary after failover | In-flight requests can still fail; replication is not exactly-once delivery |
| Cluster | Handles routing, redirects and topology changes in the driver | Topology retries and partial pipelines have separate semantics |

aiodrf adds no command retries by default. The tested standalone pool path does
not replay a command unless configured. redis-py and valkey-py retry Cluster
commands by default; the native backends pass a `retry` without retries for
Cluster unless `retry`, `cluster_error_retry_attempts` or
`connection_error_retry_attempts` is configured, so a replayed `aincr()` or
`aadd()` cannot apply twice. Redirects and topology refreshes are the driver's
routing, not command retries, and are unchanged. Sentinel defaults can still
differ between driver versions, and a driver's direct client constructor can
have different defaults from an explicit pool.
When the retry budget matters, set it explicitly and test the deployed version.
The driver references are [redis-py retries](https://redis.readthedocs.io/en/stable/retry.html)
and [valkey-py retries](https://valkey-py.readthedocs.io/en/stable/retry.html).

The backend remains usable after a connection error. A caller that receives an
error may make another independent request on the same instance. Explicit
`aclose()` is different: a contrib backend closed at lifespan shutdown cannot
reopen. Create another instance for another lifespan.

## Opt-in retries for repeatable operations

Use an async `Retry` object, not the similarly named synchronous class.
For `aiodrf.contrib.redis.AsyncRedisCache`, the following defines a separate
alias for callers whose operations tolerate replay. Start from the `native`
alias in the cache guide:

```python
from redis.asyncio.retry import Retry
from redis.backoff import FullJitterBackoff
from redis.exceptions import ConnectionError, TimeoutError

CACHES["native_reads"] = {
    **CACHES["native"],
    "OPTIONS": {
        **CACHES["native"]["OPTIONS"],
        "socket_connect_timeout": 1,
        "socket_timeout": 1,
        "health_check_interval": 15,
        "retry": Retry(FullJitterBackoff(base=0.05, cap=0.5), retries=2),
        "retry_on_error": [ConnectionError, TimeoutError],
    },
}
```

For `aiodrf.contrib.valkey.AsyncValkeyCache`, use the identical lowercase options
with `Retry` from `valkey.asyncio.retry`, `FullJitterBackoff` from `valkey.backoff`
and the exceptions from `valkey.exceptions`. Never mix Redis and Valkey objects.
The alias name is descriptive, not an access-control policy. Restrict its
callers to repeatable operations, or enforce appropriate server ACLs. Each
alias owns a separate pool, so account for both pools in the connection budget.

Two retries permit up to three attempts at the driver's retry boundary. The
overall request can include connection setup, health checks, redirects and
backoff; it is not bounded solely by `socket_timeout`. Apply an application
deadline when needed:

```python
import asyncio


async def load_summary(cache):
    async with asyncio.timeout(3):
        return await cache.aget("summary")
```

`cache` here is the lifespan-owned `native_reads` instance. Cancellation
propagates; native retry backoff yields to the event loop. Do not use negative
retry counts (unbounded retry), and do not retry authentication errors or every
exception indiscriminately. See the [native asyncio client documentation](https://redis.readthedocs.io/en/stable/examples/asyncio_examples.html)
for connection ownership and cleanup.

### django-valkey vendor backend

For `django_valkey.async_cache.cache.AsyncValkeyCache`, native connection options
belong in `CONNECTION_POOL_KWARGS`, not at the top of `OPTIONS`. Keep the
lifespan-owned factory and `CLOSE_CONNECTION=True` from the cache guide:

```python
from valkey.asyncio.retry import Retry
from valkey.backoff import FullJitterBackoff
from valkey.exceptions import ConnectionError, TimeoutError

options = CACHES["native"]["OPTIONS"]
options["CONNECTION_POOL_KWARGS"] = {
    **options.get("CONNECTION_POOL_KWARGS", {}),
    "health_check_interval": 15,
    "retry": Retry(FullJitterBackoff(base=0.05, cap=0.5), retries=2),
    "retry_on_error": [ConnectionError, TimeoutError],
}
```

Apply this only to a replay-tolerant alias. This uses the vendor's existing
configuration extension, not an aiodrf-specific retry API. See
[django-valkey async configuration](https://django-valkey.readthedocs.io/en/latest/async/configurations/).

## Writes and failure reporting

A timeout or connection error after `INCR`, `EVAL`, `SET NX` or a pipeline does
not establish that the command failed on the server. Retrying can increment
twice, change an `add()` result, extend a TTL or overwrite a concurrent writer.
Even ordinary `SET` is not necessarily replay-safe for an application's
consistency requirements. Native I/O cannot provide exactly-once execution.

For standalone/Sentinel aliases containing such writes, explicitly disable
command replay while retaining lazy socket reconnection:

```python
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

CACHES["native"]["OPTIONS"].update(
    retry=Retry(NoBackoff(), retries=0),
    retry_on_error=[],
)
```

Use the equivalent `valkey` imports for contrib Valkey; for the vendor backend,
put these options in `CONNECTION_POOL_KWARGS`. These snippets do not configure
Cluster's separate topology retry budget. Verify Cluster's settings and failure
behavior against the installed driver; do not infer a no-replay guarantee from
`retry=0` alone. Sentinel discovery options live separately in `sentinel_kwargs`.

Failures remain exceptions, not fabricated cache misses. aiodrf does not add a
global circuit breaker or hide write failures. If cache reads are optional,
handle the relevant driver exceptions in the application with a bounded
fallback and observability. Session storage, security counters and distributed
coordination usually require a different failure policy from response caching.

## Verification

The Redis and isolated Valkey service profiles cover:

- Socket disconnection followed by successful work through the same pool.
- A lost reply after the server accepted a write, without a duplicate increment
  when replay is disabled.
- A repeatable read recovering with retries explicitly enabled.
- Exhaustion of a two-retry budget and propagation of the final exception.
- Cancellation during backoff with a single-connection pool, followed by a
  successful command proving that the connection was returned.

These behaviours hold for both django-valkey's backend and aiodrf's own
backends, and Sentinel failover and Cluster commands are tested separately. The
failures are simulated by discarding a reply the client has already read; the
tests do not reproduce network partitions, so they say nothing about recovery
time or availability in production.
