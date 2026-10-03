# Measuring performance

Measure the whole request before choosing an optimization. Middleware, database
round trips, relation loading, serializer hooks, encoding and calls to other
services each dominate different workloads, and an async view does not make the
database driver asynchronous.

## Separate read and write serializers

Input and output can use different serializers without changing any DRF field.
After validating and saving, represent `write.instance` with a dedicated read
serializer through `await aio.data()`. The
[serializer backend example](../../examples/serializer-backends/README.md) does
this while keeping `aperform_create()`, the request context, success headers and
the usual errors. Reload saved relations when their order or signal handlers
affect the output: validated many-to-many input is not necessarily what was
saved.

Calling another serializer's synchronous `.data` from `to_representation()`
keeps DRF's behaviour and does not use aiodrf's compiled serializers. The
[selective optimization guide](serializer-optimization.md) explains how to
choose compiled output, field caching and copy plans independently.

## Tools

| Measurement | Tool | Notes |
| --- | --- | --- |
| Request time by await stack | Pyinstrument | Shows await stacks and the event-loop thread; not a complete profile of worker threads |
| CPU time and call counts per Python function | Yappi with its CPU clock | Includes worker threads; use call counts and self time, and do not add up overlapping cumulative times |
| Native CPU work | `perf stat`, Valgrind Cachegrind | Attach to the application process and exclude start-up; simulated instruction counts are not latency |
| Memory allocation | Memray | Shows allocation volume and stacks; allocated bytes alone do not indicate a leak |
| Serializer field construction cost | Timing in your application | Measure the first (cold) construction separately from later (warm) copies |
| Input validation and output time | Your contract tests with a profiler | Include serializers that fall back to DRF |
| Deployment behaviour | Your server, proxy and database | See [deployment behaviour](deployment-validation.md) |

Install profilers only in the environment you are inspecting, and use a
dedicated database; they are not dependencies of aiodrf.

Pick the tool for the question. Time spent inside `sync_to_async` in a sampling
profile does not show that switching threads is slow: the worker thread may be
validating input or running SQL. Inspect worker CPU and query timings
separately. `aiodrf.test.count_hops()` counts aiodrf's switches to a worker
thread, not Django's or asgiref's, so compare it with whole-process call counts
when you assess thread overhead. Keep hop counting and SQL logging off while you
measure throughput.

When you profile an ASGI application directly, note what is missing compared
with production: the HTTP transport, the production event loop and concurrent
clients. Profile complete ASGI requests, including disconnects, rather than a
view method alone, and confirm improvements with separate, uninstrumented HTTP
runs.

## Request handling and rendering

aiodrf recognizes coroutine functions from their code flags without keeping
references to callable instances. Marked functions, partials and callable
objects are inspected with the general coroutine checks. A synchronous
decorator around an async hook is not assumed to be safe on the event loop.

Profile request start-up, response rendering and cleanup as well as the
handler. Django's request signals, database connection cleanup and the async
signal receivers of installed packages can switch threads even when the view
makes no query. Removing signal receivers, or sharing one executor across
requests, changes Django's lifecycle and isolation guarantees; aiodrf does
neither on your behalf, apart from the opt-in `REQUEST_THREADS` below.

Before rendering JSON on the event loop, aiodrf inspects the response data.
Data made only of built-in types is encoded on the event loop; other values,
custom time zones and types with custom metaclasses are encoded in a worker
thread. The inspection never evaluates lazy strings, querysets or application
callbacks. It runs for every response, because middleware and view code can
modify the data. Data of built-in types is recognized quickly, in C; other data
is walked in Python, which costs CPU time on large nested payloads. A built-in
JSON renderer instance with changed attributes is always rendered in a worker
thread, because a replaced method or encoder can perform I/O. Each item of a
streaming response is handled the same way.

## Request threads

Under Django's ASGI handler, every request starts a thread for its synchronous
code, needed at least for Django's `request_started` and `request_finished`
signals, and another thread to wait for it. On inexpensive endpoints, starting
these two threads is a noticeable part of the CPU time of a request. With
`AIODRF["REQUEST_THREADS"]` and `aiodrf.asgi.get_asgi_application()`, the
threads are kept for later requests; each still serves one request at a time.
See the [setting](../reference/settings.md#request_threads) and its trade-offs
in the [tuned profile](tuned-profile.md#request-threads).

## Memory and garbage collection

DRF's objects refer to each other: the view to its request and response and
back, a list serializer's child to the list, bound fields to their serializer.
Such reference cycles are freed by Python's cycle collector rather than by
reference counting, so a request's body, its data and the instances it
serialized stay in memory until the next collection, and each collection has
to scan every request in progress.

- aiodrf's `Response.close()` breaks these references once the response has
  been sent: between the view, the request and the response, and in the
  serializer whose `ReturnList` or `ReturnDict` the response returned (its
  cached `fields`, which are rebuilt if read, and its list's child). The
  [limitations](../reference/limitations.md#middleware-and-synchronous-hooks)
  list what remains readable afterwards.
- `aiodrf.contrib.list_serializers` provides list serializers whose child refers
  to the list weakly, for data that does not reach a response as a DRF
  `ReturnList` (`Response({"items": list(serializer.data)})`) and for
  serializers used outside requests. Set one as `Meta.list_serializer_class`,
  or as `default_list_serializer_class` on a base serializer;
  `SchemaListSerializer` is the one for msgspec and Pydantic serializers.
- `AIODRF["MONKEYPATCHES"]` applies the same to all of DRF's classes in the
  process (`weak_list_children`, `release_drf_responses`), for projects that
  also run DRF's own views or third-party list serializers. It patches DRF and
  must be enabled explicitly. Its `cache_model_field_info` and
  `keep_json_encoders` patches avoid work DRF repeats for each serializer and
  each response ([setting](../reference/settings.md#monkeypatches)).

aiodrf never calls `gc.freeze()`, disables collection or changes the collector's
thresholds. Python documents `gc.freeze()` for a specific
[pre-fork procedure](https://docs.python.org/3/library/gc.html#gc.freeze), not
for a running server worker, and freezing warmed-up request objects can keep
resources alive. Change garbage collection settings only when measurements of
your application's memory justify it.

## Comparing configurations

- Keep the interpreter, dependencies, number of workers, proxy, payloads and
  queries identical, and record package versions and settings with the results.
- Alternate the order in which the configurations run, warm both up, and record
  several independent runs with their individual samples.
- Check statuses, response bodies and query counts before comparing speed: a
  fast error response is not an improvement.
- Report average and tail latency, throughput, event-loop delay, memory and
  connection limits as relevant, and do not combine unrelated workloads into a
  single score.
- Measure serializer compilation separately from warm execution, and compiled
  serializers separately from those that fall back to DRF. Do not time code
  under `override_settings()`: changing settings clears aiodrf's and
  django-fastdrf's caches.
- CPU pinning, noisy shared machines and background load are part of the
  environment. Profilers show where work happens; repeated uninstrumented runs
  measure it.

## Environment of the published measurements

The timings and instruction counts in this documentation and in the
changelog were measured on one machine. They compare configurations on that
machine; other hardware gives other numbers, so measure your own endpoints
before choosing.

| | |
| --- | --- |
| CPU | Intel Core i7-14700F: 8 performance cores (16 threads, up to 5.3–5.4 GHz) and 12 efficiency cores (up to 4.2 GHz), 33 MiB L3 cache |
| Memory | 128 GB |
| System | Ubuntu 26.04.1 LTS, Linux 7.0, bare metal |
| CPU settings | `powersave` frequency governor with turbo enabled, simultaneous multithreading on |
| Python | CPython 3.14.4 (GCC 15.2), with the GIL |
| Packages | Django 6.1.1, DRF 3.18.1, msgspec 0.21.1, Pydantic 2.13.5 |
| Services | PostgreSQL 17 in a local container, reached over loopback |

Single-process measurements ran pinned to performance cores; other services
(database containers) were running, so expect a few percent of noise. The
processor and its clock, performance or efficiency cores, the Python build
(free-threaded CPython is slower for this code), the package versions, the
payloads, the database and the network all change the results. Ratios carry
over to similar hardware better than absolute times. Instruction counts
(Cachegrind) depend less on the machine than times, but still on the Python
build and the package versions.
