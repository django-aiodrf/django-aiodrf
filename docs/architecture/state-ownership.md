# State ownership and internal helpers

Module-level state is appropriate for immutable protocol tables, application
registrations and shared caches. Request state belongs to the request or an
explicit context. Moving a dictionary into a class does not, by itself, change
its lifetime or make access thread-safe.

## Cache ownership

| State | Lifetime and bound | Synchronization and invalidation |
| --- | --- | --- |
| `utils.class_cache` | Weak class keys; no more than 1024 published entries per decorated function between capacity evictions | Hits and deterministic analysis run outside the lock. Publication, capacity eviction and explicit clearing share a lock. A generation check rejects publication after an explicit clear. |
| Field templates and copy plans | Separate `class_cache` instances in `contrib.builtin.field_cache`; values may own fields, validators and their closures | `AIODRF` and `REST_FRAMEWORK` changes clear the templates and plans. Capacity eviction also releases classes captured by validators. |
| Serializer classification | Weak class keys; values contain enums, booleans or sets of attribute names, not serializer instances | Instance mutations bypass class classification. `AIODRF` changes clear cached execution kinds. |
| Compiled input/output | At most 1024 class buckets per compiler cache, and 32 dynamic variants per class; weak keys additionally release classes without backreferences | Class-bucket publication/eviction and variant publication have separate short locks. Clearing detaches buckets, so in-flight work cannot republish a cleared bucket. `REST_FRAMEWORK` changes invalidate format-dependent results. |
| Typed schema classes and metadata | 1024 strong entries per `BoundedCache` | Construction and eviction share the cache's lock. Derived serializers, list TypeAdapters and Struct metadata reference their source classes, so weak keys alone would not release them. Backend instances and Pydantic context belong to each serializer, outside these caches. |
| Inferred relation paths | `_LookupCache` owns weak serializer and model keys, path lists and a publication lock | Hits and inspection run outside the lock. Clearing replaces the table; snapshot identity prevents in-flight work from republishing into the replacement. No serializer instance or request-specific `Prefetch` queryset is stored. |
| Content negotiation | At most 1024 header/format/renderer-description keys; only renderer indexes and media types are stored | Publication and capacity eviction share a lock, including on free-threaded Python. Accept headers longer than 256 characters are not cached. |
| msgspec deferred conversion | Weak generated-Struct keys; values describe child schemas and attribute names | The compiler finishes constructing metadata before returning an encoder. No request or serializer instance belongs in this table. |

A weak-key dictionary is not an ephemeron: if its value, or a nested cache key,
refers back to the class, that class remains reachable. Examples include a
validator closing over its serializer class and a custom field class referring
to its parent serializer. Capacity limits cover these cycles without adding
attributes to application classes or patching DRF.

Capacity eviction in `class_cache` counts successful publications, not current
live classes. Garbage collection can remove weak keys earlier; after 1024
publications, the next new entry clears the cache. This avoids per-hit recency
tracking. Eviction can repeat analysis but does not change serializer behavior.
The limits bound entry counts, not bytes: one field declaration or schema may
itself contain a large object graph. Cached callbacks must not close over users,
requests or resources acquired for a single operation.

## Registrations and constants

Bridge classes, transparent mixins and pure classes are held weakly. Pure-method
registrations store method names under weak class keys. Removing the last
application reference therefore also releases an otherwise unreferenced
registered class.

Explicit callable purity registrations, authentication credential checks and
database-vendor transaction factories belong to application initialization,
normally module imports or `AppConfig.ready()`. They retain their registered
callables for the application lifetime. Do not register request-local closures
or repeatedly construct new registrations during request handling. Registration
is not a concurrent runtime configuration API.

Exact-framework class sets, field-hook name tuples, regular expressions and
sentinels are finite protocol data. They remain at module scope; wrapping them in
mutable service objects would add ownership machinery without a benefit.
The OpenTelemetry tracer is an API proxy for the application's provider, not a
span or request cache. The application owns exporter queues and shutdown.

## Lifespan resources

`LifespanApplication` holds a factory, not an entered context or a resource.
The server's lifespan scope owns a private marker shared by shallow request
scope copies. Before cleanup, invalidation marks it inactive and clears its
resource reference, including when context cleanup subsequently fails. Keeping
an old request therefore does not retain a closed pool through that marker.
Application-owned references outside the marker are not cleared automatically.

Access performs no I/O and takes no lock. Initialization and teardown belong to
the owning event loop; request handlers must finish their resource-using tasks
before shutdown. A stale scope is rejected, and the marker's reference is
released after both successful and failed cleanup. The
[lifespan guide](../guides/lifespan.md) describes composition and server support.

## Hop diagnostics

`count_hops()` owns a `HopCounter` for the duration of its block. Its `ContextVar`
contains a weak reference, so copied contexts and detached child tasks cannot
retain a completed diagnostic. Closing and recording share a lock; once the
block exits, inherited tasks stop recording even if the caller keeps the result.
Exceptions and cancellation close the scope and restore the previous context.

Nested blocks count independently: the innermost active context receives a hop,
and leaving it restores the outer context. Child work must be awaited inside
the block if its hops are part of the measurement.

The result retains function names, not callable objects or request arguments.
`count` is derived from `calls`, preserving the existing diagnostic API. The list
has no silent truncation; its size is proportional to calls in that explicit
scope. Use it around a test or bounded operation, not the server lifespan, and
release the result when it is no longer needed.

## Helpers used by other modules

These names are internal, but not unused. Their callers are documented in their
docstrings or adjacent comments; they are not added to the public API to silence
an editor diagnostic.

| Helper | Consumer and purpose |
| --- | --- |
| `aio._classify._resolve_unknown` | `aio._validate` resolves the current unknown-validation policy at each execution stage. |
| `aio._common._bridged` | Validation and saving determine whether a synchronous serializer hook can reach an async override. |
| `aio._common._sync_member` | Validation, representation and saving select the synchronous default without re-entering a bridge recursively. |
| `aio._common._acall` | Validation and representation share the `utils.invoke` adapter. |
| `aio._common.NEEDS_AWAIT` | Synchronous serializer operations signal that the event-loop half must continue. |
| `policies._throttle_wait` | `APIView` and the ADRF compatibility adapter obtain a rejected throttle's wait duration in the appropriate execution context. |
| `utils._transparent` | The OpenTelemetry mixin declares that its instrumentation must not change hook selection. |
| `contrib.builtin.field_cache._fields_are_static` | `ModelSerializer.get_fields()` and the automatic prefetch inspector decide whether field definitions can be reused. |
| `contrib.builtin.relations._batch_related_lookups` / `_unbatch` | The validation walker applies an optional instance-local relation adapter and restores it in `finally`. |

Django signal receivers and management-command methods also have callers outside
their defining file. Import/reference inspection must precede deletion. A Ruff
unused-import finding is different from an editor finding no local call to an
internal helper.

## Verification

aiodrf's tests check, with weak references and explicit garbage collection,
that these structures release what they hold: copied contexts, cancelled work,
nested diagnostics, registrations, validators, compiler caches at capacity and
prefetch state, also under concurrent access. aiodrf never forces a garbage
collection while handling requests. These checks do not replace measuring the
memory of your application, its third-party caches and exporter queues under
its own workload.
