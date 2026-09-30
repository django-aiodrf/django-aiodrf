# CPU-bound work in async applications

Async I/O and CPU parallelism solve different problems. An async handler can
release the event loop while waiting for a socket; Python serialization or
computation continues to occupy its executing thread until it returns or yields.
Changing a server or adding an `async` declaration does not remove that work.

## Existing execution behavior

aiodrf already adapts unknown synchronous hooks, validators, filters and renderers
through worker execution. Generic actions group compatible synchronous operations
instead of scheduling each field independently. Explicit async-safe declarations
and eligible compiled operations can execute inline; their CPU cost still matters.
See [thread boundaries](../architecture/thread-boundaries.md).

FastAPI similarly offloads synchronous endpoint/dependency functions it invokes,
but a synchronous helper called directly inside an async endpoint is still the
application's responsibility. This is event-loop protection, not a guarantee of
multicore execution. See [FastAPI's async explanation](https://fastapi.tiangolo.com/async/#path-operation-functions).

## Selecting an execution strategy

### Thread-pool APIs

The [Sentry comparison](https://sentry.io/answers/fastapi-difference-between-run-in-executor-and-run-in-threadpool/)
contrasts a framework helper with asyncio's configurable executor API. Its
"default executor" wording should not be read as a shared pool: current
[Starlette implementation](https://github.com/Kludex/starlette/blob/main/starlette/concurrency.py)
calls `anyio.to_thread.run_sync`, not `loop.run_in_executor(None, ...)`.
The [Starlette pool documentation](https://github.com/Kludex/starlette/blob/main/docs/threadpool.md)
describes a default 40-token limiter shared by work using that AnyIO limiter.
Changing asyncio's default executor does not tune that limiter.

| API or framework | Scheduling contract | Implication for aiodrf |
| --- | --- | --- |
| FastAPI sync endpoints and dependencies | Framework-scheduled worker execution; helpers called inside async code are not automatically adapted | Already addressed for DRF hooks by aiodrf's explicit sync/async bridge |
| Starlette `run_in_threadpool` | AnyIO worker execution and capacity limiter | Suitable for independent blocking libraries, not a replacement for Django thread affinity |
| `asyncio.to_thread` | Loop executor with copied context variables | Convenient for isolated blocking calls; no Django connection-affinity guarantee |
| `loop.run_in_executor` | Explicit executor or loop default; context propagation is the caller's responsibility | Supports a dedicated process pool; lifecycle and admission are application-owned |
| Litestar `sync_to_thread` | Documented asyncio executor or Trio worker/limiter, selected by runtime | Explicit scheduling is preferable to detecting CPU work from a function name |

The framework comparison follows [FastAPI's execution contract](https://fastapi.tiangolo.com/async/#path-operation-functions),
[Litestar's concurrency reference](https://docs.litestar.dev/main/reference/concurrency.html)
and [Python's executor reference](https://docs.python.org/3/library/asyncio-eventloop.html#asyncio.loop.run_in_executor).
Worker placement and multicore speedup are different decisions. In particular,
do not create an executor with a `with` statement inside every async request:
pool creation repeats, and synchronous shutdown can wait on the event-loop thread.

### Workload selection

| Work | Initial choice | Constraint |
| --- | --- | --- |
| Django ORM, DRF hooks with lazy model access | Existing thread-sensitive worker path | Preserve database affinity and transaction ownership |
| Blocking SDK without thread affinity | A bounded application-owned worker pool where necessary | Bound admission as well as threads; timeout/cancellation cannot stop a running thread |
| Small serializer/encoding operations | Existing DRF or measured opt-in compiled path | Extra scheduling can cost more than the operation; native implementation does not imply GIL release |
| Long pure-Python computation | Separate worker processes or a task service | Payload transfer and scheduling must be worth the cost; keep request/model objects out of process payloads |
| Durable work after a write | Existing task integration with explicit transaction/delivery policy | An in-process executor is not a durable queue |

On ordinary GIL-enabled CPython, adding threads does not parallelize pure-Python
CPU work across cores. Offloading can keep the loop responsive, but threads
still compete for the GIL and add scheduling overhead. Extensions that release
the GIL and free-threaded builds need their own evidence and dependency checks.
See [Python's `to_thread` guidance](https://docs.python.org/3/library/asyncio-task.html#asyncio.to_thread).

Do not add a global serializer executor. It would complicate model affinity,
transaction boundaries, instance ownership and shutdown, while duplicating the
worker adaptation already present. Do not set `thread_sensitive=False` for ORM
code merely to increase parallelism. A process pool must be worker-owned, started
after process creation and closed during lifecycle shutdown; avoid nested pools
when a task worker or application process manager already supplies parallelism.

Cancellation needs its own policy. Cancelling an awaiter does not reliably stop
an already running thread or process job. A semaphore released when that awaiter
is cancelled may admit more work while the original job is still running. Bound
outstanding submissions through completion, define an overload response and test
shutdown before introducing a custom executor. Do not send requests, serializers,
database connections or context-bound model objects to a process worker.

The recommended default remains unchanged: existing thread-sensitive adaptation
for Django work, direct awaits for asynchronous clients, and an application-owned
process/task boundary for sufficiently large CPU jobs. No automatic serializer
offload threshold or new global executor is added. Evaluate loop delay, p95/p99,
CPU time and throughput with cancellation/overload tests before promoting a
workload-specific policy to a framework feature.

## Reducing CPU and allocation work

1. Profile the complete request before changing scheduling. Separate validation,
   query execution, field construction, representation and JSON rendering.
2. Remove duplicate representation passes. A write serializer and dedicated read
   serializer can preserve persisted output without invoking a nested synchronous
   `.data` path that bypasses compilation.
3. Select field caching/copy plans only for static declarations and test custom
   constructors, binding and validators. Prefer endpoint/serializer scope first.
4. Bound page and payload sizes. Work avoided often matters more than moving the
   same large operation to another thread.
5. Use explicit relation loading and batch lookups before adding concurrency.
   Avoid N+1 queries and keep authorized queryset scope intact.
6. Compare backend and renderer combinations with response/error parity checks.
   A faster JSON encoder does not remove DRF authentication, validation or response
   finalization. Keep fast-parity differences explicit.

Measure throughput and tail latency alongside loop delay and memory. Do not
infer the source of a framework-to-framework gap from throughput alone: differing
middleware, response construction, transaction scope and authentication are
confounders. Run one-setting-at-a-time comparisons before adding framework-level
execution machinery. Public guides intentionally do not embed local benchmark
scores; [measurement methodology](performance.md) defines the evidence required.
