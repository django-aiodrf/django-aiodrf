# django-aiodrf framework design

This document describes the `django-aiodrf` distribution and its `aiodrf`
Python package and its integration with Django REST framework.

aiodrf is an async execution layer over DRF. It retains DRF's declarations,
objects and extension conventions, then determines where each operation must
execute. It does not replace Django's HTTP handler, ORM, authentication backend
system, middleware stack or URL resolver. Its optional serializers and HTTP
extensions are separate from that compatibility contract.

## 1. Objectives and non-goals

The primary objective is incremental migration: an application can change a
view's base class and introduce async I/O without rewriting its serializers,
permissions, routing or schema definitions. Existing synchronous extension code
must remain usable without blocking the event loop when the framework invokes
it. Async operations must preserve DRF's stage order and error behavior.

Performance work reduces redundant introspection and thread crossings. It must
not bypass authentication, reorder validation, discard custom overrides or
evaluate unknown user code speculatively on the event loop. A lower hop count
is not sufficient evidence of lower latency or fewer database queries.

The package does not promise that every DRF method becomes awaitable, that
all third-party subclasses work unchanged, or that standard Django database
operations become native async driver calls. It does not provide a new ORM,
dependency injection container, task broker, routing language or OpenAPI engine.
The package is currently alpha; compatibility guarantees must be read alongside
the [support policy], [extension contract] and named test scenarios.

## 2. Ownership and module boundaries

| Layer | Implementation | Responsibility |
| --- | --- | --- |
| HTTP transport and application services | Django | ASGI/WSGI dispatch, middleware, sessions, connections, models, migrations and signals |
| API semantics | DRF | Field definitions, serializer metadata, errors, negotiation, renderers, parsers, policies, routers and response conventions |
| Async orchestration | `views`, `request`, `generics`, `mixins`, `policies`, `aio` | Await supported operations; adapt synchronous work; preserve override selection and execution order |
| Execution metadata | `utils`, `_builtins`, `hooks`, `settings`, `compat` | Pair resolution, worker entry, known inline operations, configuration and version-dependent behavior |
| Optional extensions | `contrib`, `asgi`, streaming classes, management commands | Typed schemas on async serializers, lifecycle ownership, streaming, batching and named ecosystem adapters |
| Deployment experiments | `unsafe.middleware` | Explicit middleware scheduling alternative, disabled by default |
| Verification and migration | `test`, `checks`, `codemod`, `tests` | Async clients, diagnostics, reviewable migration edits and compatibility contracts |

`REST_FRAMEWORK` continues to configure DRF authentication, permissions,
renderers, parsers, filters and pagination. `AIODRF` controls only additional
execution policies and opt-in features. The serializer optimizations
(compilation, input recognition, field caching, related lookups, fetch modes)
come from django-fastdrf, a dependency, and are configured with `FASTDRF`. Optional libraries are imported when
their integration is selected; installing msgspec must not silently change the
serializer backend.

The [implementation guide] maps these layers to execution paths.

## 3. Request execution

The default lifecycle retains DRF's ordering:

1. Construct the DRF-compatible request and select parsers/authenticators.
2. Perform content negotiation and versioning, then authentication, permissions
   and throttling.
3. Evaluate configured conditional-request hooks and dispatch the handler.
4. Convert handled exceptions through DRF's exception contract.
5. Finalize the response, including renderer context and headers.
6. Let Django render/send the response and close the request's resources.

The request object adds `auser()`, `aauth()` and `adata()`. These use the same
authentication and parsed-data state as DRF's synchronous properties; they are
not a second authentication or parsing system. An async handler should use
these entry points when lazy access may perform I/O. Multipart parsing, spooled
bodies, storage operations and custom parsers retain worker boundaries.

There are three execution categories:

| Category | Placement | Important limit |
| --- | --- | --- |
| Supported coroutine hook | Await on the request loop | Blocking code inside an application's `async def` still blocks that loop |
| Proven or explicitly declared nonblocking synchronous operation | Inline | CPU work still delays other tasks; nonblocking does not mean inexpensive |
| Other synchronous hook | `run_sync`, using `sync_to_async(thread_sensitive=True)` | Cancellation cannot forcibly stop work already running in the thread |

Django's ASGI request context supplies thread affinity. `run_sync` is the common
aiodrf boundary, not an independent executor or connection pool. A request can
retain a worker while awaiting an external service; “async” does not imply one
thread for the entire deployment. Standard Django `aget()` and `asave()` still
adapt synchronous ORM work in the supported implementations.

`count_hops()` uses a `ContextVar` to record aiodrf-managed boundary calls.
Django's signal handling, middleware adaptation and ORM wrappers are outside
that counter. [Thread-boundary budgets] assert representative paths, not a
universal one-crossing-per-request guarantee.

## 4. Method-pair resolution and reverse bridges

### 4.1 Public naming

Handlers and routed actions retain DRF names: `get`, `post`, `list`, `create`,
`retrieve`, `update`, `partial_update` and `destroy`. Their implementations may
be coroutines. The router still identifies an action as `list`, not `alist`.
This preserves schema array detection, operation names, action-specific
permissions and serializer selection.

Hooks that coexist with a synchronous protocol have an `a` counterpart:
`get_queryset`/`aget_queryset`, `perform_create`/`aperform_create`,
`get_serializer`/`aget_serializer`, `get_serializer_class`/`aget_serializer_class`,
`get_serializer_context`/`aget_serializer_context`,
`get_authenticate_header`/`aget_authenticate_header`,
`has_permission`/`ahas_permission`, `save`/`asave`. This follows Django's naming
convention without making every factory or field callback asynchronous.

### 4.2 Override selection

`resolve_pair(object, sync_name, async_name)` selects the implementation, not
just whether its name begins with `a`. It first inspects instance overrides,
then method owners in the class MRO. An async instance override wins over a
sync instance override; otherwise the closest custom class definition wins,
with the async definition winning a same-class tie.

| Resolution | Meaning |
| --- | --- |
| `Impl.ASYNC` | The async-named member is selected |
| `Impl.SYNC` | A custom synchronous member is selected |
| `Impl.SYNC_IS_ASYNC` | The original DRF name contains coroutine-producing code |
| `Impl.BASE` | Neither member is an application override; use the framework default |

Resolving only `type(instance)` cannot observe an override installed on that
instance. Class-level answers can be cached; instance decisions must remain
outside that cache. This matters for dynamically customized serializers and
policy objects, including hooks that redact output or restrict access.
Conditional ETag and Last-Modified hooks use the same instance-aware selection.
Binding a default async hook evaluates its descriptor once, rather than probing
it with `hasattr()` and then binding it a second time.

### 4.3 Registration is not monkeypatching

`bridge_base(cls)` records the exact class as a provider of framework defaults.
It returns the class unchanged. Registering DRF's base serializers or mixins
does not replace their methods, add async APIs to them or declare them safe to
execute on the loop. The module-level registration loops are startup metadata,
not a per-request class rewrite.

`bridges_to("a...")` has a separate responsibility. It decorates a synchronous
method on an aiodrf-owned class so a synchronous DRF caller can reach a selected
async override. From a worker, `async_to_sync` can return to the request loop.
Calling that bridge synchronously on an already-running event loop is invalid;
use the async entry point there.

These are an adaptation mechanism and an override protocol. They are not a
general-purpose plugin registry, dependency injection mechanism or permission
to decorate arbitrary third-party classes as framework defaults. The dedicated
[bridge reference] explains MRO examples, reverse calls and failure cases.

### 4.4 Coroutine-producing wrappers

An ordinary `def` decorator can perform blocking work before returning a
coroutine. Detecting its wrapped `async def` does not make the prefix safe.
`invoke()` runs an unknown synchronous prefix in the worker and awaits its
returned awaitable on the loop. Only known transparent wrappers or explicit
coroutine declarations use the inline path.

The cancellation handoff in `run_sync_and_await` closes an unstarted native
coroutine returned after cancellation. It does not cancel arbitrary background
tasks created by that callable or reverse database/external side effects.

### 4.5 Serializer dispatch versus default operations

`aio.is_valid`, `aio.save`, `aio.data` and `aio.to_representation` are dispatchers:
they select user overrides. `default_*` operations implement the framework path
without redispatching to the same override. Async base methods call the latter
so `await super().ato_representation(...)` does not recurse into the subclass.

`_sync_member` finds the appropriate synchronous implementation while skipping
the framework's reverse wrappers. `_async_serializer_bridge` is a separate,
method-specific marker for that fallback. `NEEDS_AWAIT` is a private sentinel:
it distinguishes an incomplete synchronous stage from legitimate results such
as `None`, `False` or an empty dictionary.

## 5. Generic actions and continuation state

For conventional CRUD, wrapping every query, field and hook separately would
add boundaries around work that DRF already executes sequentially in a thread.
aiodrf's action bodies group that synchronous work. The private `Step` states
identify the points at which an async operation requires returning to the loop:
object permissions, serializer construction, validation, persistence,
representation or response work. `Step.SERIALIZE` is used only when a selected
serializer factory hook is async; conventional synchronous construction stays
inside the existing worker body. Async factory results retain backend allowlist
checks and schema adaptation. Constructors still run in the worker.

The action resumes after the awaited stage. It does not rerun completed stages,
reorder side effects, or use an exception as a signal to retry the entire
operation. This is a small continuation protocol inside CRUD, not a general
workflow engine. The DRF methods these continuations follow are tracked for
every supported DRF version, so changes in a DRF release are reviewed before it
is supported.

## 6. Serializer compatibility

### 6.1 Reuse of DRF declarations

Fields, relations, serializer metaclasses, `Meta`, `many=True`, context and
error classes remain DRF's. Plain DRF serializers work through the `aiodrf.aio`
facade and generic views. The aiodrf serializer bases add awaitable operations
and reverse bridges; they do not require a parallel family of async fields.

The preferred spelling is `await serializer.adata()`. The callable/awaitable
descriptor also supports ADRF's `await serializer.adata` during migration.
Plain DRF objects do not gain these methods; use `await aio.data(serializer)`.

Model-specific return annotations are declared under `TYPE_CHECKING` where
runtime forwarding methods would change method ownership. In particular, the
current `ModelSerializer.save/asave` type declarations do not insert another
runtime wrapper ahead of the serializer bridge. A method added only to refine
a return type can otherwise alter `_sync_member` selection or enter an owned
transaction twice. Type specialization must be reviewed for runtime MRO effects,
not treated as automatically behavior-neutral.

### 6.2 Validation order and errors

A synchronous serializer can validate through DRF directly in one worker
segment. A mixed serializer uses a stage walker: field conversion, field
validators, `validate_<field>`, serializer validators and object validation.
Synchronous runs are grouped, async stages are awaited in order, and DRF's
`ErrorDetail`, partial/default behavior and field error ordering are preserved.

Custom ordinary field conversion methods and defaults must remain synchronous.
A custom `run_validation` or `run_validators` cannot necessarily be split around
async field validators without changing its semantics. Unsupported combinations
raise `ImproperlyConfigured`; put awaited work in a supported serializer hook.
The mixed-validation walker does not replace these methods temporarily.

An independent, opt-in related-lookup optimization does temporarily adapt
`to_internal_value` on eligible serializer-owned relation instances and restores
it in `finally`. It does not change DRF classes. This distinction matters when
reviewing mutation and concurrency: serializer instances are request-owned and
must not be reused concurrently. See section 8 for its narrower contract.

### 6.3 Representation

Synchronous representation may trigger lazy model queries, custom properties or
storage access; its default is the worker. Async method fields, supported model
sources and nested serializers are walked with ordered synchronous segments.
Exact known scalar conversions can remain inline; a module name alone is not
a sufficient safety claim for arbitrary field conversion.

`REPRESENTATION_MODE="inline"` is an application assertion that the selected
representation cannot block, not an automatic prefetch operation. Defaults,
missing attributes, custom `.data` properties, instance overrides and redaction
hooks must retain their original behavior. Compilers must decline unsupported
overrides rather than bypass them.

### 6.4 Writes and transaction ownership

`ATOMIC_SAVE=True` wraps the default synchronous create/update and associated
many-to-many writes in Django's `transaction.atomic()` on the routed write
alias. It is not an atomic request, a distributed transaction, or a wrapper for
arbitrary `save`, `acreate` and `aupdate` implementations. A custom save owns its
own transaction policy. `ATOMIC_REQUESTS` is not supported for these async views.

Put multi-operation transactional work in a synchronous function and adapt the
whole unit. Do not hold a synchronous transaction across unrelated awaited I/O.
Worker cancellation does not prove rollback; application retries need explicit
idempotency. Native-backend transaction behavior is separate (section 12).

## 7. Compiled serialization and typed schemas

These are distinct features, not interchangeable names for an async serializer.

| Mechanism | Source of truth | Behavior on unsupported input or structure |
| --- | --- | --- |
| Compiled DRF output | Existing DRF serializer | Use DRF, or raise an eligibility error when configured |
| Compiled DRF input recognition | Existing DRF serializer | Decline recognition and let DRF validate and report errors |
| Schema-first serialization | msgspec Struct or Pydantic model | Use the schema library's validation/coercion rules, translated to DRF responses |
| JSON parser/renderer | Selected codec | Encode/decode JSON through the DRF parser/renderer interface |

### 7.1 Selection and precedence

`FASTDRF["SERIALIZER_BACKEND"]` selects `drf` (default), `msgspec` or `pydantic`.
`Meta.serializer_backend` overrides it per serializer, including opting back
out to `drf`. Parity and fallback settings control output eligibility. The
allowlist `ALLOWED_SERIALIZER_BACKENDS` instead controls permitted serializer
*kinds* (DRF declaration, Struct, model); it is not a second output-backend switch.

For DRF declarations, compilation applies where aiodrf executes validation or
representation: generic actions, the `aio` facade and async serializer methods.
An ordinary direct synchronous `.data` or `.is_valid()` call is not globally
replaced. Select optimization through the documented awaited operations.

### 7.2 Structural analysis and backend strategies

The compiler, input recognition, field caching and copying, related lookups
and auto-prefetch are maintained in
[django-fastdrf](https://github.com/ctolon/django-fastdrf), the synchronous
package aiodrf depends on; the former `aiodrf.contrib` modules were removed, so
they are imported from `fastdrf`. aiodrf registers its serializer bases with fastdrf
(`bridge_base` calls `fastdrf.utils.framework_base`), keeps its own
classification of what may run on the event loop (`aio._classify`), and
decides where the compiled code runs.

`fastdrf.compiler` analyzes fields, model sources, custom hooks and relation
shapes into a reusable specification. The msgspec and Pydantic compiler modules
and the dependency-free python backend (`fastdrf.output`) turn
eligible specifications into backend-specific encoders. Keeping eligibility
separate from encoding avoids asking a backend's permissive coercion rules to
define DRF compatibility.

Compilation is whole-serializer, per direction. A custom field, data hook or
unsupported source can decline the entire direction. Eligibility is inspected
by class only for static declarations; dynamic fields require an instance field
signature. Nested static analysis includes collection children. A class owning
its output is rejected before unnecessary field construction.

Strict output mode supports a conservative set of concrete model fields,
foreign-key identifiers and eligible nested relations. It accounts for Django
descriptors and manager behavior; dictionaries from `values()` are not assumed
to behave like model instances. The [serializer backend guide] lists exact
field coverage and fallback rules.

The msgspec encoder builds an immutable completion plan alongside the generated
schema. It converts related-manager values after the initial attribute conversion,
then visits only children with deferred relations of their own. Scalar-only child
schemas need no second traversal. Plans retain child schemas, not their root key;
the registry uses weak keys so temporary root schemas remain collectible. Manager
access, relation ordering and DRF fallback remain unchanged.

For loaded values in inline representation mode, the compiled/DRF dispatcher
also owns async-hook classification. The caller does not classify the same
serializer separately. Unevaluated querysets still retain the worker boundary
unless an async representation hook owns their evaluation.

Strict mode may fall back to DRF after a recognized backend/source failure.
That can reread an eligible Django source. This controlled representation
fallback is distinct from executing an unknown hook on the loop and retrying it.
Custom property/manager behavior must not be admitted on that assumption.

`"fast"` mode intentionally broadens eligibility and can change behavior, such
as Decimal formatting or missing-attribute errors. Datetime output in the
supported fast path still calls DRF's timezone conversion. Identical output for
some payloads does not mean strict parity for every model value.

### 7.3 Input recognizer

`fastdrf.inputs` accepts canonical values only when DRF would produce the same
`validated_data`. Exact built-in field types and understood constant constraints
define that subset. Coercions, custom validators, relation lookups and malformed
values are left to DRF. Recognition does not invent a second error format.

Input rejection always returns to DRF, including when the *output* fallback
setting is `"error"`. The Pydantic path does not require msgspec. Hypothesis
tests compare recognized values and types with DRF, not merely whether both
implementations returned a successful status.

### 7.4 Cache and loaded-column fast paths

Static classes can reuse a compiled encoder or a cached refusal without building
fields on every instance. Dynamic serializers use bounded per-class variants
(32 per direction). Backend and parity participate in the key; relevant settings
changes invalidate compiled state.

A previously compiled flat model encoder may represent an exact model instance
on the loop when every required column is already in its instance dictionary.
Deferred fields, relations, nested serializers and cold analysis keep their
worker boundary. This is a loaded-source check, not a global “models are safe”
assumption.

### 7.5 Schema-first adapters

`MsgspecSerializer`, `PydanticSerializer` and `typed.SchemaViewMixin` adapt typed
schemas to DRF's serializer interface. A bare Struct or model may be selected as
`serializer_class`; input/output schema pairs can be resolved when the URL is
built. `validated_data` remains a dictionary for DRF save hooks, while
`validated_object` exposes the typed value.

Backend instances are serializer-scoped. Pydantic receives the serializer's
context for validation and serialization; msgspec receives explicit per-serializer
decode, encode and JSON Schema hooks. Bounded class/TypeAdapter caches do not own
request context. Msgspec's fixed schema metadata is cached, while value extraction
uses its shallow `structs.asdict()` API and does not re-inspect type annotations.

Native outputs are not necessarily mappings. The contrib data property preserves
RootModel/scalar and array-like outputs, attaching DRF's serializer backlink only
to mappings and lists. List input stays attribute-keyed for DRF save hooks;
representation restores its native input shape before applying the output schema.
Pydantic's allowed extra fields remain in validated data. These policies are
native-schema contracts, not an expansion of strict DRF compiler eligibility.

Synthetic read-only `.fields` describe output for metadata and tooling. They
do not turn the browsable API into a generated editable schema form. Aliases,
form values, error trees and partial representations have dedicated tests.
Automatic partial-schema derivation is rejected when it would omit custom
validation invariants; supply `Meta.partial_schema` in that case.

Schema-backed default model writes assign plain fields, not arbitrary nested
relations. `schema_response()` is synchronous representation on its calling
thread; do not pass an unloaded model graph from an async handler and assume
that method offloads it.

### 7.6 Inspection and conversion tools

`aiodrf_inspect_serializers` reports input/output eligibility, stable reason
codes and endpoint usages, including uninspectable dynamic factories. Static
inspection is not request tracing. Constructors may run during inspection and
must be suitable for use without a live request.

django-fastdrf's `fastdrf_convert` generates schema/serializer source and notes unsupported or
lossy constructs. Generated source requires review; it is not proof that two
validation languages are equivalent. JSON codec selection remains independent
of both tools and of the compiler.

## 8. Query and serialization optimizations

The optional field-copy policy, plans, template cache, related-key batching and
inferred query loading are django-fastdrf's (`fastdrf._field_copy`,
`_field_cache`, `_relations`, `fastdrf.prefetch`); aiodrf keeps
`contrib.builtin.list_prefetch` and `concurrent`, and `aiodrf.contrib.prefetch`
remains a compatibility export of `PrefetchListSerializer`. Core serializers and
generic views retain thin selection points so existing settings and overrides
continue to work.
Dynamic field trees are inspected per request. Inferred query paths use a weak-key
table; clearing replaces that table, and snapshot identity rejects publication
from an older calculation. Hits and inspection run outside the publication lock.
Django's own fetch-mode API remains a version-gated generic-view
integration, not a replacement ORM implementation.

| Feature | Mechanism | Compatibility boundary |
| --- | --- | --- |
| `Meta.auto_prefetch` | Derive relation loading from serializer fields | Keep queryset scoping and request-specific `Prefetch` objects local; custom sources may need explicit plans |
| `FETCH_MODE` | Select supported Django queryset fetching behavior through `compat` | Version-dependent and off by default; a system-check error (`fastdrf.E006`) before Django 6.1; not a new ORM |
| `CACHE_SERIALIZER_FIELDS` | Cache an unbound static model-field template, deepcopy for each instance by default | aiodrf model serializer bases only; dynamic field hooks and runtime model/Meta changes are not a safe template contract |
| `FIELD_COPY_MODE` | Optional prepared field-copy plans | Defaults to `deepcopy`; `clone` optimizes exact scalar fields, while `compiled` recursively prepares supported nested/container construction. Unknown or custom behavior uses DRF deepcopy. Select through serializer `Meta`, view attributes or settings; no DRF class is patched. |
| `BATCH_RELATED_LOOKUPS` | Validate eligible to-many primary keys through one `pk__in` query | Preserve input order and first-error behavior; custom relation/queryset behavior stays on DRF |
| `PrefetchListSerializer` | Materialize the page, load specified ORM relations, await `aprefetch()` once, then represent | Batch hook is application-owned; pagination still matters |
| `ConcurrentListSerializer` | Construct one independent serializer per active item, await bounded tasks, preserve output order | Experimental; representation only, no concurrent validation or writes |

Field-template copies retain DRF's normal copying semantics, including sharing
validator objects passed as constructor arguments. The template itself is never
bound to a request. Dynamic field-building hooks, callable model metadata and
`Meta.depth` prevent using a class-wide template.

Batched related lookup changes SQL shape and query count. It falls back for
unsupported querysets and ambiguous keys, and keeps repeated input entries
distinct. It is opt-in because custom code can observe evaluation patterns.

Batching external I/O is preferable when a service supports it. Concurrent
representation requires an explicit `get_item_serializer()` factory because
copying a bound child can share fields, context or constructor state. Results
are O(n) in memory; at most `max_concurrency` tasks are active. The limit is
per call, not per process. Failure cancels and joins outstanding tasks, but a
noncooperative finalizer can delay cleanup. This path is experimental: it can
increase the tail latency of small requests handled by the same worker.

## 9. HTTP extensions and resource ownership

### 9.1 Rendering and streaming

The response fast path marks supported rendering as awaitable for Django's
async handler. A structural payload check admits known exact types; lazy values,
querysets, custom callbacks and post-render callbacks retain worker handling.
This preserves `cache_page`'s store-after-render contract. Large safe JSON can
still monopolize the loop; there is no justified automatic size threshold yet.

Payload inspection is iterative and runs for each render, without retaining
response values in a process cache. Unknown container types are rejected;
custom metaclasses are rejected before type membership tests. Datetime values
also require a known timezone implementation. Renderer instance attributes can
replace methods or encoders, so modified instances of the built-in JSON
renderers use the worker path even when the payload is plain data. This rule
also applies to streaming items.

`StreamingResponse` produces NDJSON, `StreamingArrayResponse` a JSON array,
and `EventStreamResponse` SSE. The response owns its producer and exposes
`aclose()`. Synchronous source construction, chunk reads and cleanup stay in
the worker. Async producers are closed on completion or cancellation. SSE
keepalive maintains one pending producer context, rather than canceling and
restarting each item; metadata rejects line injection and invalid retry values.

Django sends streaming chunks and the proxy controls downstream buffering.
WSGI can materialize async iterators and is unsuitable for live or infinite
streams. Middleware wrapping another owned iterator must close it explicitly.
When sending fails, Django does not always close the response: its ASGI
handler does not close it when sending the headers fails, and its
`streaming_content` wrapper does not close its source when a send fails.
Acquire resources inside the producer where possible, and release them in a
`finally` block.

### 9.2 Typed lifespan state

`asgi.get_asgi_application()` wraps Django for the ASGI lifespan protocol.
`DJANGO_LIFESPAN` accepts a zero-argument async context-manager factory or
dotted import path. It enters before startup notifications, publishes its
yielded value under namespaced application state, and exits after shutdown,
including failure and cancellation paths. `get_lifespan_state(request, Type)`
checks liveness and type.

Request scopes share a private lifetime marker, not a global resource lookup.
Cleanup invalidates the marker and releases its resource reference before
closing the context. Old request copies therefore cannot expose or retain the
resource through that marker. HTTP dispatch delegates to the wrapped ASGI
application without entering contexts, allocating state copies or acquiring locks.

The factory is configuration; its yielded client/pool is a runtime resource.
Resources are created on the serving loop, not at import or in `AppConfig.ready`.
Each worker/lifespan owns its own resources. It is not a process-global singleton
or a cross-worker registry. Dependent resources belong in one context manager
or `AsyncExitStack` with explicit ordering.

`asgi_startup` and `asgi_shutdown` are Django robust async signals. Receivers
must own their cleanup and not rely on receiver order. A failed startup does
not guarantee a later shutdown notification. Context-manager ownership is the
appropriate mechanism when partial startup needs deterministic rollback.

### 9.3 Conditional requests, query parameters and HTTP QUERY

Conditional hooks run after view-level authentication, permissions and
throttling. They use Django's precondition handling and return 304 or DRF-style
412 responses as appropriate. Object scoping/permissions are not implicitly
checked by computing an ETag: use a scoped object lookup. An `If-Match` check
followed by a save is not an atomic compare-and-swap.

`query_serializer_class` provides explicit query-string validation, cached on
the view instance after the handler asks for it. It is not automatic rejection
of all undocumented query parameters. QueryDict and repeated-key behavior stay
DRF's; optional spectacular schema support describes the fields.

The main package dispatches HTTP QUERY, validates its content requirements and
offers test-client helpers. It does not patch DRF's `SAFE_METHODS`, Django CSRF,
CORS settings, URL-only cache keys or OpenAPI's operation set. Those integration
limits are detailed in the [extension contract] and [implementation guide].

### 9.4 Cache and throttling

`aiodrf.cache.cache_page` composes Django's `CacheMiddleware`, using the worker
for nonlocal synchronous backends and preserving keys, Vary and cache-control
semantics. Application code still owns user/tenant cache partitioning. Streaming
and QUERY are not made safely cacheable by this decorator.

`aiodrf_async_cache.middleware` provides explicit native middleware. Request-local cache
snapshots let Django's own middleware decide keys, Vary and cacheability; missing
reads and recorded writes are awaited against a lifespan-owned backend. The view
is never replayed. No security policy is copied or process-wide method replaced.
The Redis and Valkey contrib backends share a private `BaseCache` implementation
and bind different native client classes. Each backend belongs to one event loop
from construction, with instance-owned clients and shutdown callbacks. Async
key/value/default hooks are awaited; unknown synchronous callbacks use the normal
thread-sensitive bridge. Standalone, Sentinel and Cluster connection selection
does not alter Django's key/version/TTL contract. Cross-slot batches use individual
commands without transactions. Synchronous middleware uses a separate alias.
The vendor django-valkey integration also has an instance-owned factory to avoid
its process-global URL-only pool registry. See the [async cache guide](guides/async-cache.md)
for lifecycle, callback and plugin boundaries.

OpenSearch's `AsyncDocumentWriter` is a separate composition adapter: public
django-opensearch-dsl preparation/ID/predicate hooks execute in the worker, while
an externally owned `AsyncOpenSearch` performs HTTP. It does not mutate document
registries or install signal receivers. The application selects index publication
timing and durability; no SQL/search distributed transaction is implied.

`FixedWindowRateThrottle` uses cache `add`/`incr` rather than DRF's history-list
update. Its concurrent correctness depends on backend atomicity and eviction
behavior. Fixed-window boundaries allow bursts; this is not a replacement for
an external abuse-control or distributed billing quota system.

## 10. State, synchronization and memory

| State | Owner and lifetime | Review requirement |
| --- | --- | --- |
| Request auth/data, view instances and serializer fields | One request or explicit serializer operation | Never share mutable serializer/view instances across concurrent requests |
| Bridge/purity/transparent registrations | Import/app initialization; class membership is weak | Explicit callable registrations must not capture requests or clients |
| Class metadata | Weak-key caches, capped at 1024 publications per decorated function | Capacity eviction bounds value-to-key cycles; a weak key alone does not prove collectability |
| Compiled variants | 1024 class buckets per cache, 32 dynamic variants per class | Enforce both bounds under synchronization; invalidate on relevant configuration changes |
| Schema adapters | `BoundedCache`, 1024 entries per cache | Strong bounded entries and identity reuse; arbitrary cache mutation is not a public extension API |
| Settings | DRF-style lazy configuration | Validate before publishing; generation-aware reload, not a transaction spanning an entire request |
| Negotiation metadata | Bounded process cache | Store renderer position/media type, not a request or live renderer instance |
| Hop tracing | Block-owned record, weak reference in inherited contexts | Count only owned crossings; stop recording and reset context on exit |
| HTTP clients/pools | Lifespan or explicit context | Close on their owning loop; do not cache them as serializer metadata |

`class_cache` computes outside its publication lock. Concurrent callers can
duplicate deterministic analysis; a generation change prevents stale analysis
from being republished after a clear. Hits do not acquire the publication lock.
Arbitrary concurrent rewriting of classes or model metadata is not supported.

The [state-ownership reference](architecture/state-ownership.md) lists
cache owners, retained objects, invalidation rules and internal helper consumers.
Prefetch paths belong to one cache object; clearing replaces its weak-key table.
Request-specific `Prefetch` querysets remain outside it.

The main package still has broader package-based `is_framework_class` checks
in some introspection paths, even though pair resolution and several compiler
checks use exact registration. That remaining heuristic is a maintenance
risk, not a security sandbox.

## 11. Ecosystem integration

Compatibility is maintained through the original object model and extension
protocols, not by replacing ecosystem packages:

* Concrete generic views also inherit their DRF counterparts. Routers and
  schema introspection retain familiar class relationships and action names.
* DRF field instances, QuerySets, errors, request context and response metadata
  stay visible to filtering, dynamic-field, nested-write and renderer packages.
* Synchronous vendor hooks run in the thread-sensitive worker. A vendor's own
  `async def` implementation remains responsible for avoiding blocking calls.
* `contrib.django_filters` re-exports django-filter rather than creating a new
  filter language. Native querysets need a separate adapter.
* `contrib.spectacular` registers normal spectacular extensions for aiodrf auth
  and typed serializers; optional `AutoSchema` describes query serializers and
  `StreamSchema` describes streaming items. Projects install schema/Swagger
  URLs and their permissions explicitly. No public documentation endpoint is
  silently added.
* SimpleJWT, Knox and auth-kit opt-in apps register known credential-presence
  checks. They avoid unnecessary work on credential-free requests without
  skipping verification or a user lookup when credentials exist.
* Model/object permission contribs preserve Django's synchronous backend chain
  where needed, including third-party backends implementing only that protocol.
* `TracingMixin` adds phase spans without changing pair precedence. The project
  owns provider, sampling and exporters. Request bodies are not span attributes,
  but unexpected exception recording can still include sensitive messages;
  exporter policy/redaction needs application review.

The [ecosystem matrix] records versions and scenarios for additional packages:
authentication, nested serializers, tenant context, caches, Channels, storage,
middleware and developer tools. Each entry covers the scenarios it describes,
not every feature or version of the package.

## 12. Specialized integrations and deployment boundaries

`contrib.async_backend` uses django-async-backend's native PostgreSQL manager,
views, serializer writes and pagination. The external package patches Django
model classes when its app loads; that behavior is not part of the default
aiodrf architecture.

Native reads and writes do not make validation, lazy relation reads, prefetch
or synchronous signal receivers native. Native and Django connections are
distinct. The adapter coordinates owned write boundaries and receiver callbacks,
but it does not provide a distributed transaction; receiver writes can contend
with rows locked by the native connection. Query caches that hook Django's SQL
compiler cannot be assumed to observe native operations. See the [native
backend guide] before selecting this integration.

`contrib.mongodb` provides ObjectId relation support and a vendor transaction
factory for the main package's owned default save. Django's generic atomic
context is not sufficient for that backend. Topology and backend-version tests
are separate from PostgreSQL verification.

`unsafe.middleware` changes scheduling only through selected subclasses and
an explicit setting, default false. Both replacement paths and the flag are
required. Stock Django classes are not monkeypatched, middleware order must
remain semantically correct, and `DJANGO_ALLOW_ASYNC_UNSAFE` is not enabled.
This is a deployment experiment, not a prerequisite for async views.

`contrib.adrf_compat` is an explicit exception to normal import behavior: it
aliases ADRF modules and supplies migration adapters when enabled. The codemod
is the reviewable long-term migration path. Neither compatibility shim nor
unsafe scheduling is required by core async dispatch.

## 13. Management commands, diagnostics and migration

`AsyncCommand` retains Django argument parsing, checks, output and exit behavior
while running `ahandle()` through `async_to_sync`. Thread-sensitive ORM work
then uses the command's calling-thread connection ownership. `acall_command`
is the awaitable entry point from an existing loop. Optional command lifespan
resources are scoped to the handler; ASGI startup/shutdown signals are not sent
for a non-ASGI command. Default signal handling cancels and joins the handler,
while application-installed handlers remain respected.

Django system checks report invalid settings, unsupported async hooks, excluded
serializer kinds and deployment risks such as persistent ASGI connections or
sync-only middleware. Checks do not prove a live deployment's capacity or open
database connections merely to inspect configuration.

Migration starts with one endpoint and unchanged serializer/policy declarations.
Introduce async I/O through supported hooks, then validate DRF parity, query
counts, cancellation and schema output. Introduce each performance option
separately. The codemod should first run in diff mode; ambiguous constructs need
manual review. Rollback is a base-import/configuration change only while the
application has not adopted additional async-only hooks or schema semantics.

## 14. Extension examples and rules

The [independent example catalogue](../examples/README.md) provides uv projects
with their own ASGI applications, tests and configuration. Its
[feature evaluation matrix](../examples/FEATURES.md) covers every aiodrf setting
and separates normal behavior from opt-in/experimental integrations.

Tasks deliberately require no aiodrf runtime abstraction: Django 5.2 projects
may install the optional django-tasks 0.12 backport; Django 6 projects use
`django.tasks` directly. Application code selects the matching import and
backend namespace. Both expose `aenqueue()`. The framework does not own the
worker, delivery retries or an outbox. See the [task contract](guides/tasks.md).

### Async queryset scoping

```python
from aiodrf import generics


class ArticleList(generics.ListAPIView):
    queryset = Article.objects.all()
    serializer_class = ArticleSerializer

    async def aget_queryset(self):
        tenant_id = await self.request.tenant_service.current_id()
        queryset = await super().aget_queryset()
        return queryset.filter(tenant_id=tenant_id)
```

Here `Article`, `ArticleSerializer` and the nonblocking `tenant_service` are
application dependencies. `filter()` constructs a scoped queryset; evaluation
still follows the selected database execution path. For asynchronous selection
or context loading, use `aget_serializer_class()` or
`aget_serializer_context()`. DRF metadata and ORM optimization may also request
the serializer class, so selection hooks should support those callers.

### Async validation with DRF fields

```python
from rest_framework.exceptions import ValidationError

from aiodrf import serializers


class AddressSerializer(serializers.Serializer):
    address = serializers.CharField()

    async def validate_address(self, value):
        if not await self.context["directory"].accepts(value):
            raise ValidationError("This address is not available.", code="unavailable")
        return value
```

The application owns the directory client and timeout. Do not make
`CharField.to_internal_value` async or share this serializer instance across
tasks. A network-backed validator makes input recognition ineligible; DRF's
validation path still owns the result.

### Per-serializer compilation

```python
class PublicArticleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Article
        fields = ["id", "title"]
        serializer_backend = "msgspec"
        serializer_backend_fallback = "drf"
```

Use this through aiodrf generic views or its async serializer operations and
install the selected extra. Output parity is the global
`FASTDRF["SERIALIZER_BACKEND_PARITY"]` setting, whose default is `"strict"`;
there is no per-serializer parity option. A later custom representation hook should trigger
fallback, not disappear behind the compiled output. Test that behavior before
using compilation on an authorization-dependent field projection.

An integration should use public DRF hooks first. A new contrib may need a
private aiodrf seam, but each such dependency is recorded with its reason. Do
not turn private bridge/classification
registries into a project-level configuration API merely to avoid a worker hop.

## 15. Implemented patterns and their limits

| Pattern | Concrete use | Constraint |
| --- | --- | --- |
| Adapter/facade | `aio` operations, async request access, typed serializers | Preserve the underlying DRF object contract; do not claim schema rules equal DRF rules |
| Template method | DRF lifecycle and CRUD hooks | Preserve ordering, headers, errors and override ownership |
| Explicit method-pair bridge | `resolve_pair`, `bridge_base`, `bridges_to` | Separate default registration from reverse dispatch and purity |
| Continuation state machine | CRUD `Step`, serializer `NEEDS_AWAIT` | Resume at known cut points without replaying completed user code |
| Strategy with conservative eligibility | DRF/msgspec/Pydantic backends | Eligibility precedes execution; fallback is part of the contract |
| Bounded/weak memoization | Class metadata, compiled variants, typed schema cache | Audit invalidation, publication races and retention graphs |
| Batch loading | ORM prefetch, external `aprefetch`, related-key lookup | Preserve ordering and scope; do not confuse batching with parallel transactions |
| Bounded task ownership | Concurrent list representation and SSE producer | Join owned tasks on failure/cancellation; application timeouts remain necessary |
| Context-manager resource ownership | Typed lifespan and command resources | Resources belong to a loop/lifespan, not to imported module globals |
| Decorator and transparent mixin | Async-safe declarations and tracing | A declaration is a contract; transparency must not conceal business behavior |
| Ports to existing ecosystem protocols | Spectacular extensions, Django checks/apps, vendor adapters | Keep optional libraries optional; validate each supported integration scenario |

These names describe mechanisms already present in the code, not a requirement
to add abstraction layers. A new feature should have a concrete caller,
an explicit ownership boundary and a regression that demonstrates its need.

## 16. Verification

Each layer is verified separately: unit tests of override precedence,
validation order, callbacks, lazy I/O, defaults, errors and cancellation; DRF's
own test suite run against aiodrf's views; side-by-side comparisons with DRF;
third-party packages at recorded versions; PostgreSQL, native backend and
deployment tests; generated input for the compiled serializers; and concurrent
and free-threaded runs. [Compatibility with DRF](guides/compatibility.md)
describes how compatibility is checked and lists the known differences. The
[performance guide] describes how to measure the opt-in optimizations with your
own workload.

[support policy]: guides/releasing.md
[extension contract]: guides/extension-hooks.md
[implementation guide]: guides/implementation.md
[thread-boundary budgets]: architecture/thread-boundaries.md
[bridge reference]: architecture/bridge-pattern.md
[serializer backend guide]: guides/msgspec-pydantic.md
[ecosystem matrix]: guides/ecosystem.md
[native backend guide]: guides/async-backend.md
[performance guide]: guides/performance.md
