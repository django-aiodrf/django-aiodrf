# Tuning

Change one thing at a time and keep a reference configuration to compare
against. An optimization is worth keeping only if it improves your workload
while your API still behaves as it should. The [performance guide](performance.md)
describes how to measure and which profiling tools to use.

## Baseline

Start with aiodrf's default settings, your usual Django middleware, paginated
querysets and a supported ASGI server. Record the number of queries, throughput,
the latency distribution, event-loop delay and memory use. Include invalid
input, authorization failures and client disconnects in the workload, and
measure the first requests after start-up separately from the rest.

## Query and response design

1. Remove repeated database reads with explicit relation loading.
2. Use batch enrichment when the external service supports a batch request.
3. Separate read/write serializers when creation responses have a different
   representation. Represent the saved instance, not the submitted input.
4. Select a compiler only after checking field eligibility and strict parity.

An input serializer with relation fields or custom validation may spend its
entire validation step on DRF's fallback path. In that case, select
`Meta.serializer_backend = "drf"` for the write serializer and choose a compiled
backend for the separate read serializer. Field caching and relation batching
remain independent choices. Unsupported input field types are rejected before
building a compiler cache key; that does not remove DRF field construction,
validation or relation queries.

## PostgreSQL connections under ASGI

For Django's standard PostgreSQL backend, use `CONN_MAX_AGE=0` under ASGI.
This disables persistent Django connection wrappers across requests; a driver
pool can still retain physical connections for reuse. Django's pool requires
psycopg 3 and `psycopg[pool]`, not psycopg2. Closing a Django connection returns
it to the pool. See [Django's database guidance](https://docs.djangoproject.com/en/6.1/ref/databases/#connection-pool).

The following is a starting profile, not a universal optimum. Apply it to an
already configured PostgreSQL alias with application-owned credentials:

```python
DATABASES["default"].update(
    {
        "CONN_MAX_AGE": 0,
        "OPTIONS": {
            **DATABASES["default"].get("OPTIONS", {}),
            "pool": {
                "min_size": 2,
                "max_size": 10,
                "timeout": 5,
                "max_idle": 300,
                "max_lifetime": 1800,
            },
        },
    }
)
```

| Option | Effect | Sizing consideration |
| --- | --- | --- |
| `min_size=2` | Target minimum open connections per initialized pool | Multiplied by workers, replicas and aliases; lower for many mostly idle instances |
| `max_size=10` | Maximum connections per pool | Increase only with DB capacity and observed pool contention; not equal to HTTP concurrency |
| `timeout=5` | Maximum wait for a pool connection, in seconds | Not a SQL or HTTP timeout; fit it within the endpoint's total deadline |
| `max_idle=300` | Retire surplus idle connections after five minutes | Does not shrink below `min_size` or terminate active queries |
| `max_lifetime=1800` | Recycle old connections after roughly 30 minutes | Pool-managed replacement, not an active transaction deadline; the pool adds jitter |

These meanings come from the [psycopg pool API](https://www.psycopg.org/psycopg3/docs/api/pool.html#psycopg_pool.ConnectionPool).
`connect_timeout` controls establishing a physical connection; PostgreSQL
`statement_timeout` controls statement execution. Configure them separately when
the service requires them. `CONN_HEALTH_CHECKS=True` can detect stale pooled
connections at checkout, with additional check overhead. It is not a pool-size
or throughput optimization.

Each process has a pool per database alias. For one alias, three replicas with
four workers and `max_size=10` can consume 120 connections, plus jobs, management
commands and administrative capacity. Rolling deployment overlap can temporarily
double the application portion. With different aliases, sum their pool maxima
before multiplying by workers/replicas. Keep that budget below the database or
pooler's usable connection limit.

Measure pool wait time, pool timeouts, SQL duration, transaction duration and DB
CPU before increasing the maximum. A larger pool can worsen database contention.
One CPU-limited container should normally start with one worker; choose more
workers only alongside the [server and memory budget](web-servers.md).

`CONN_MAX_AGE=0` does not return a checked-out connection on every `await`.
Avoid opening a DB connection before a long unrelated external request when
application semantics allow a different order. Do not call connection cleanup
from an arbitrary thread or share Django connection objects across workers.
Keep transactions in one synchronous unit as described in
[async transactions](async-transactions.md).

PgBouncer transaction pooling is a separate deployment choice. Review
server-side cursor/session-state restrictions and both pool budgets; stacking
poolers does not create database capacity. The profile above does not select a
native async driver or remove Django's ORM adaptation.

## Configuration profiles

| Profile | Settings | Intended use |
| --- | --- | --- |
| Default | No overrides | Reference DRF-compatible execution |
| Strict compiler | SERIALIZER_BACKEND=msgspec, pydantic or python | Supported output/input paths without opting into fast-parity differences; python compiles output only, with no dependency |
| Cached fields | CACHE_SERIALIZER_FIELDS=True | Static model serializer declarations; each request receives independent fields |
| Scalar field cloning | Cached fields plus FIELD_COPY_MODE=clone | Avoid repeated initialization of exact built-in scalar fields; unsupported fields use deepcopy |
| Recursive field-copy plans | Cached fields plus FIELD_COPY_MODE=compiled | Precompute nested/container copy operations, preserve constructors and custom deepcopy; selectable per serializer or view |
| Related lookup batching | BATCH_RELATED_LOOKUPS=True | Eligible many-valued primary-key input fields |
| Data responses | Handlers return `fastdrf.response.DataResponse` | JSON answers without DRF's template response; `data` is not kept after rendering, and middleware that expects DRF's `Response` attributes or `process_template_response` does not apply |
| Tuned | All of the above; see the [tuned profile](tuned-profile.md) | The fastest configuration, for workloads that meet its conditions; not a general default |

The [serializer backends example](../../examples/serializer-backends/README.md)
provides normal, strict-msgspec, strict-pydantic, tuned, tuned-clone and
tuned-compiled profiles, selected with `AIODRF_EXAMPLE_PROFILE` before the
process starts. Run your own tests with every profile you use.

## Field-copy limits

`FIELD_COPY_MODE="clone"` only applies when field-template caching is enabled.
The current plan recognizes exact BooleanField, CharField, IntegerField,
BigIntegerField where available, FloatField, UUIDField and ReadOnlyField.
It does not bypass custom constructors, relation querysets, nested serializer
binding, date/decimal behavior or application-owned argument copying.
Those cases retain DRF deepcopy.

Supplied validators remain shared as in DRF. Generated validators and lazy
validation messages are independent, so each request gets its messages in its
own language. The `UniqueValidator` DRF builds for a unique model
field or a single-field `UniqueConstraint` is shared by every copy, so its
message is formatted when it is read, in the request's language. The model
managers DRF passes to the relation fields it builds stay shared by reference,
as in DRF, in every copy mode. Template classes, models and Meta declarations
must remain static. Changing `FASTDRF` or `REST_FRAMEWORK` clears both
templates and copy plans. Copy plans are an internal optimization you enable explicitly; they do
not replace `Field.__deepcopy__` for other code.

`FIELD_COPY_MODE="compiled"` additionally prepares recursive constructor-copy
plans for containers and nested serializers. Custom constructors and deepcopy
hooks still execute; unsupported nodes retain DRF copying. Prefer serializer
Meta or view attributes for endpoint-specific activation. See
[selection precedence and compatibility](serializer-optimization.md).

Measure application serializers before enabling the option. Separate cold plan
construction from warm copying and include validation, database access, rendering
and concurrent requests in endpoint-level decisions.

## Event-loop and process limits

Large JSON encoding and compiled serialization remain CPU work. Fewer thread
transitions do not imply lower tail latency under load. Keep arbitrary sync
renderers and validators in workers unless their nonblocking behavior is
established. Do not apply gc.freeze(), change Django middleware or select a
native database adapter merely to improve a microbenchmark.

Adding a thread pool is not a general CPU-throughput optimization. On a
GIL-enabled Python build, ordinary Python work does not execute in parallel
across threads. Offloading can protect loop responsiveness, but adds scheduling
and context-transfer work; measure throughput and tail latency separately.
Extensions that release the GIL and free-threaded builds require their own
measurements. See [Python's thread-offloading guidance](https://docs.python.org/3/library/asyncio-task.html#asyncio.to_thread).
Keep ORM operations and transactions on their existing thread-sensitive path;
do not pass Django connections or request-bound serializers to an arbitrary
executor. Use additional ASGI worker processes only within the available CPU,
memory and database connection budget.

For PostgreSQL review pooling, transaction duration and connection lifecycle
with the selected backend. For streams verify proxy buffering and cleanup
through your actual server and proxy. See
[deployment behaviour](deployment-validation.md) for what to verify in your
environment.
