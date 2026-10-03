# Thread-boundary accounting

`aiodrf.test.count_hops()` records calls through `aiodrf.utils.run_sync` in
the current context. Creating an adapter does not count as calling it. The
counter is a regression diagnostic, not a latency or total-thread metric.

Outside a diagnostic scope, the wrapper still performs a `ContextVar.get()` and
a conditional check before awaiting the thread-sensitive adapter. It does not
acquire the counter lock or append a record. Inside a scope, recording adds a
weak-reference lookup, synchronization and a function-name append. These are
real costs, but their share of endpoint latency requires measurement; a hop
count alone cannot quantify them. Do not enable recording for a throughput run.

The scope owns the counter; inherited task contexts hold only a weak reference.
Exiting the block closes recording, including on exceptions or cancellation.
Await child tasks inside the block to include their work. Nested blocks count
independently and restore the outer context on exit. Retained results contain
function names only, but their size grows with the number of measured calls;
do not keep a diagnostic open for the server lifespan. See
[state ownership](state-ownership.md#hop-diagnostics).

## Thread affinity

Synchronous ORM operations and unknown DRF extensions use
`sync_to_async(..., thread_sensitive=True)`. Django's ASGI handler supplies the
request context. Do not infer deployed concurrency from calling a coroutine
outside that handler: asgiref's executor selection depends on the context.

A synchronous generic action can validate, load and represent data in one
worker unit. An overridden asynchronous hook may require additional transitions.
The bridge resolver preserves application override order; it does not combine
unrelated hooks merely to reduce a count.

## Transitions outside the counter

Django signals, middleware adaptation, ORM `a*` wrappers, third-party adapters
and ordinary response rendering can introduce transitions outside this counter.
Use application-level tracing when investigating the complete request.
