# Request execution and compatibility boundaries

This guide describes the implemented execution paths. The evaluated alternatives are in the
[architecture decision](../architecture/alternatives.md).

Terminology: a **hop** is one call of `aiodrf.utils.run_sync`, which runs a
function in the request's thread (`sync_to_async(thread_sensitive=True)`).
`aiodrf.test.count_hops()` records every hop with the name of the function. It
counts aiodrf's hops only: Django's own (signals, a synchronous `render()`,
synchronous middleware) are not included.

## 1. The execution rule

Code runs once, where its classification puts it:

- `async def` members are awaited on the event loop, in DRF's order.
- Synchronous code aiodrf knows to be free of I/O runs inline on the loop:
  DRF's and Django's own classes registered in `aiodrf/_builtins.py`, and what
  a project declares with `@async_safe`, `async_safe = True`,
  `AIODRF["PURE_POLICIES"]` or `AIODRF["INLINE_RENDERERS"]`.
- Other synchronous hooks run in the thread-sensitive worker. Contiguous work
  can share a hop; intervening async stages introduce additional boundaries.
- Nothing is tried on the loop first and repeated in a thread. There is no
  retry and nothing is learned from earlier requests. A
  `SynchronousOnlyOperation` out of code that was declared safe propagates.

Purity is transitive (`utils.is_pure`): the class that defines a method must be
declared pure, and the classes between it and the object's class may only add
plain data. A `JSONRenderer` subclass that sets `encoder_class`, or a
`SearchFilter` subclass that overrides `get_search_fields`, is not pure by
inheritance. The five DRF permission classes are registered as leaves: their
methods call nothing, so `class IsOwner(IsAuthenticated)` that adds
`has_object_permission` keeps an inline `has_permission`.

## 2. Sync/async pairs

Supported async extension points use pairs (`get_queryset` / `aget_queryset`,
`has_permission` / `ahas_permission`, `create` / `acreate`, ...).
`utils.resolve_pair(obj, sync_name, async_name)` checks instance overrides first,
then decides which class member is the project's. Only the class calculation is
cached weakly; passing just a class cannot account for an instance override:

| Result | Meaning | Async caller | Sync caller |
| --- | --- | --- | --- |
| `ASYNC` | the async member is user code, or the nearer of two | awaited | `async_to_sync` |
| `SYNC` | only the sync member is user code | inline if pure, else one hop | called |
| `SYNC_IS_ASYNC` | the sync name holds an `async def` (adrf style) | awaited | `async_to_sync` |
| `BASE` | neither is user code | framework default | framework default |

Classes registered with `utils.bridge_base` (aiodrf's and DRF's bases) do not
count as user code. `call_pair` serves async callers, `call_pair_sync` sync
ones: DRF's synchronous `check_permissions`, `check_object_permissions`,
`check_throttles` and `Request._authenticate` are overridden to evaluate
policies through `aiodrf.policies`, so a permission that only implements
`ahas_permission` denies from a legacy `initial()` override, the browsable API
and schema generation as well. Serializer and view bridges (`is_valid`,
`save`, `run_validation`, `to_representation`, `create`, `update`,
`get_queryset`, `get_object`, `filter_queryset`, `paginate_queryset`,
`perform_*`) follow the same table and reach the user's override, not
aiodrf's default.

A view that overrides none of the request lifecycle (`initialize_request`,
negotiation, `initial`, authentication, permission and throttle checks,
conditional-request hooks, `finalize_response`, `default_response_headers`),
negotiates with DRF's `DefaultContentNegotiation`, has no versioning and uses
framework parser, renderer, authentication and request classes follows a
**request plan** (`views._class_plan`), decided once per class. It builds the
objects DRF builds (the request, its parsers, authenticators and negotiator,
the selected renderer) and sets the same state, without asking on each request
which hooks are overridden and where they may run. Every request checks that
the view is still configured as planned: nothing set on the instance, and the
parser, renderer and authentication class lists equal to the planned ones
(changed in place or replaced, they put the view on the generic path);
permission and throttle classes are read on every request, as DRF reads them.
The steps must be `APIView`'s own, or aiodrf's `ViewSetMixin.initialize_request`,
whose `action` the plan sets as the mixin does (`"metadata"` for `OPTIONS`,
else the action mapped to the method), before negotiation and the policies.
Another framework class that overrides one (DRF's own `ViewSetMixin` found
first, a tracing mixin) keeps the generic path.

Permissions have a plan of their own (`views._permission_plan`), decided once
per view class and permission classes: when the view keeps DRF's
`get_permissions` and `permission_denied` and every class is a synchronous,
pure permission without a constructor of the project's, a request builds the
permissions, authenticates first if one may read the user, and runs DRF's
loop on the event loop. Operators (`A | B`), asynchronous or impure
permissions and permissions with state of their own take the step-by-step
path. `get_object`'s object permissions have the same kind of plan
(`views._object_permission_plan`): plain classes whose `has_object_permission`
is synchronous run DRF's `check_object_permissions` loop without classifying
the permission instances on each request. The decisions rest on purity and
override answers, and are dropped with them: `register_pure`, `async_safe`, a
settings change or a bridge registration made later is seen by the next
request (`utils.depends_on_classification`). Authenticators (whether a class is DRF's
session authentication, has a registered credentials check, or is called)
and the rate throttles' hooks are decided per class the same way; the
credentials check itself is looked up on each request.

`async_to_sync` from a worker returns to the loop the request runs on. It is a
safety net for legacy call chains; aiodrf's own actions never rely on it (section 4).
The [bridge-pattern guide](../architecture/bridge-pattern.md) explains
the registrations, reverse direction, MRO and fallback in detail.

## 3. Validation

Ordinary DRF field conversion methods and defaults must remain synchronous.
Put async work in serializer validation hooks or async validators. A custom
field `run_validation()` or `run_validators()` cannot be split around async
field validators safely: this combination raises `ImproperlyConfigured`.
aiodrf does not temporarily replace those methods. Move that awaited work to the
serializer's `validate_<field>()`; standard empty, blank, nullable relation and
collection rules remain supported. See the
[compatibility boundaries](../reference/limitations.md).

`aiodrf.aio` is a package. Its public functions (`__all__`) are implemented
in private modules: `_classify` (what may run on the loop, the per-class
cache), `_validate`, `_represent`, `_save` and `_common` (`NEEDS_AWAIT` and
the fallback to a serializer's synchronous member). `_classify` and `_common`
are shared by the operation modules; their private helpers are not dead code
merely because no caller appears in the defining file. See
[internal helper consumers](../architecture/state-ownership.md#helpers-used-by-other-modules).

`aio.is_valid` dispatches through the pair table, then classifies the
serializer instance once (`_classify.validation_kind`, stored on the
instance):

- no async member anywhere: DRF's own `is_valid()` runs, in one hop, or inline
  when every field, validator and hook is pure;
- otherwise the walker (`_validate._stages`) runs DRF's stages in DRF's order, per
  field: conversion (empty values, defaults, `to_internal_value`), each
  validator, `validate_<field>`; then the serializer's validators and
  `validate`. Contiguous synchronous stages share a hop; an async stage ends
  the run. A field stops at its first failing stage, other fields continue,
  and `errors` keep declaration order.

Classifying builds the serializer's fields. It runs on the loop only for a
declarative serializer (`_classify.is_declarative_class`, also the rule for
`query_serializer_class`, section 11): no class of the project's before
DRF's in its MRO or its declared fields' defines a function, and building
fields from its model runs none of the project's code. That code is a
callable `choices` or `limit_choices_to`, a `limit_choices_to` applied
through the related model's default manager when its `get_queryset()` is not
Django's (DRF filters the relation's queryset when it builds the field), a
model field class that is not Django's, DRF's or aiodrf's (DRF reads its
attributes; third-party fields such as django-money's count), and a
`FilePathField`, which lists its directory. Any other serializer is built in
the worker hop that classifies and validates it. The per-class field templates
(`CACHE_SERIALIZER_FIELDS`) and the compiler's by-class lookup use the same
model rule.

Conversion and validators are classified separately, so a
`PrimaryKeyRelatedField` with an async validator looks its object up in a
thread and then awaits the validator. The stage walker explicitly preserves
the supported `Field`, `CharField`, relation and collection rules, including
blank handling and the relations' `'' -> None`; it does not replace methods
on a field instance. Custom validation methods that cannot be split safely
are rejected as described above. `validate_<field>` is found
with `getattr` on the instance, as DRF finds it. Stock `ListField` and
`DictField` children may have async validators, including nested collections.
Their children are validated sequentially, preserving DRF's index/key errors,
defaults and parent-validator order. Custom collection classes or instance
conversion overrides with async children still raise `ImproperlyConfigured`:
their conversion cannot safely be split around child validation.

The separate `BATCH_RELATED_LOOKUPS` option temporarily adapts
`to_internal_value` on eligible, serializer-owned relation instances, then
restores it in `finally`. It does not change DRF's field classes. This is not
the validation walker's method selection described above; serializer
instances must still not be shared across concurrent operations. See the
[related-key optimization](../reference/settings.md#batch_related_lookups).

## 4. Generic actions

Each action has a synchronous body owned by aiodrf that mirrors DRF's mixin
(`mixins.py`: `_create`, `_list`, `_list_page`, `_retrieve`, `_update`,
`_destroy`) and runs in one hop: queryset, filtering, pagination, the lookup,
object permissions, validation, the save, the representation. Calling DRF's
`super().list()` in a thread instead would skip `optimize_queryset`, the
compiled backends and `ATOMIC_SAVE`.

Where something must be awaited the body stops and returns a `Step`
(`CHECK_OBJECT`, `SERIALIZE`, `VALIDATE`, `PERFORM`, `REPRESENT`, `RESPOND`); the action
awaits that step on the loop and re-enters the body. The cut points are an
`aget_queryset`, async object permissions, `aget_serializer*`, `aperform_*`, and async validation
or representation. The plain `ModelViewSet` cases in the execution tests use
one aiodrf-managed hop; this is not a budget for arbitrary policy stacks or
Django's own middleware, signals and rendering. The DRF methods these bodies
follow are tracked for every supported DRF version, so a DRF release that
changes one is detected before it is supported.

The lifecycle around the action:

| Operation | Placement |
| --- | --- |
| `initialize_request`, the request class, parser and authenticator factories | inline; one hop if the view overrides one of the hooks, or if `request_class` or a configured class has a constructor (`__init__` or `__new__`) of its own (a constructor is code; `register_pure_method(cls, "__init__", leaf=True)` declares one pure) |
| content negotiation, versioning | inline for DRF's classes; one hop otherwise |
| authentication | session: async, no hop; header-based: no hop without credentials (`register_credentials_check`), else one hop; `aauthenticate`: awaited |
| permissions, throttles | inline if all are pure, one hop for all of them otherwise, one by one if any is async; `get_permissions`/`get_throttles` written by the project, and classes with constructors of their own, are built inside that hop |
| rate throttles | inline with an in-process cache (`cache.is_in_process_cache`), else in the hop |
| a denying throttle's `wait()` | with the throttle's `allow_request` when that runs in a hop; otherwise inline for DRF's and aiodrf's own `wait()` and one declared pure, one hop for each other one. An async `aallow_request` or a pure `allow_request` says nothing about `wait()` |
| handler | awaited; a synchronous handler runs in one hop. A synchronous decorator around an `async def` handler also runs in that hop, and the coroutine it returns is awaited on the event loop, unless it is marked with `markcoroutinefunction` (or is drf-spectacular's `extend_schema_view` wrapper) |
| exception handler | DRF's is inline; custom handler/context work is offloaded. Authentication errors await the selected challenge hook first; unknown synchronous constructors and header hooks run in the worker |
| `finalize_response` | inline; one hop if overridden |
| request body | parsed inline for DRF's `JSONParser`/`FormParser` and a body Django kept in memory (`CONTENT_LENGTH <= FILE_UPLOAD_MAX_MEMORY_SIZE`); one hop otherwise (spooled bodies, multipart, parser subclasses) |

django-filter runs unchanged inside the action's hop;
`aiodrf.contrib.django_filters` only re-exports it.

## 5. Representation and rendering

`aio.data` runs DRF's `serializer.data` in one hop (`REPRESENTATION_MODE =
"thread"`) or on the loop (`"inline"`, the project's assertion that instances
are loaded). Serializers with async members (`ato_representation`, async
`SerializerMethodField` methods, async model attributes) are walked field by
field; contiguous synchronous fields share a hop, or run on the loop under
`"inline"`: the setting applies inside the walker too.

In thread mode two cases are represented on the loop because they provably
make no query. First, a model instance (of the serializer's model itself, not
a subclass) whose static serializer class already compiled to an encoder that
reads only columns through Django's descriptor
(`contrib.compiler.loaded_encoder`), each of them loaded in the instance's
`__dict__`.
Second, DRF's own representation of model instances by a static serializer
(`aio._loaded.reads_loaded`): its class's *read plan*, built once, lists what
each field reads — a column (DRF's value fields, `PrimaryKeyRelatedField`'s
key), a forward relation for a nested serializer, a forward many-to-many or
reverse foreign key for a nested list — and each instance must have it
loaded: the columns in its `__dict__`, the related object in
`_state.fields_cache` (or a null key), the list in
`_prefetched_objects_cache` under the manager's key, level by level. A method
field, a custom field or hook, a file field, `source="*"`, a deferred column,
a relation that is not cached, a `Prefetch(to_attr=...)`, an unevaluated
queryset or an instance of another class keep the hop. So does a
representation of more than `MAX_CHECKED_OBJECTS` (32) objects: checking an
object is far cheaper than a hop, but the cost of checking grows with the
number of objects.

aiodrf's `Response` renders on the event loop when the renderer is DRF's own
`JSONRenderer`, aiodrf's msgspec renderer, or one declared pure
(`INLINE_RENDERERS`): its `render` is marked as a coroutine function, so
Django's async handler awaits it instead of hopping to a thread. For DRF's and
the msgspec renderer, a structural check before encoding accepts only known
exact payload types: `dict`, `list`, `tuple`, DRF's `ReturnDict`/`ReturnList`,
`OrderedDict` (still returned by older paginators) and JSON leaves. A mapping
with `items` set on the instance is a callback and is refused. A payload of
exact built-in types only (a cached page, a document from a native driver) is
recognized at C speed by `marshal`, which refuses every other type, subclasses
included, without calling it; the structural walk applies to the rest, and
skips the built-in subtrees `marshal` accepts. DRF's `JSONRenderer` renders
with the encoder its `render` would build, kept (the same class attributes and
encoder class, so the same bytes). Lazy values,
unknown subclasses and custom timezone callbacks select the worker; no
application callback is run speculatively or retried. Synchronous callers
still get a rendered response directly. A renderer the project declared pure
renders inline, with no second attempt. Any other renderer, and any response
with post-render callbacks (`cache_page`), is rendered by Django as usual.
This avoids an additional rendering adapter for eligible payloads; its effect
depends on payload size and the application's concurrency.

When a response closes (Django's handler closes it once it is sent),
`Response.close()` cuts the back-references among DRF's request objects: the
response in its own `renderer_context`, `view.response`, the bound `head`
Django's `View.setup` gives the view, and the view and request in the request's
`parser_context`. The view, the request, its body and the payload are then
freed by reference counting instead of waiting for the cyclic collector. The
serializer whose data the response returned as DRF's `ReturnList` or
`ReturnDict` (also as a value of a returned dictionary, a paginated page) is
released too: its cached `fields`, which DRF builds again if they are read,
and its list's reference in the child, which becomes a weak proxy. The
response's `data`, its `renderer_context` view and request and their other
attributes stay readable after `close()`, as tests read them.

### Data responses

`DataResponse` (opt-in) is Django's `HttpResponse`. The view resolves it where
DRF finalizes a response (`response.resolve_data_response`,
`aresolve_data_response`): when the accepted renderer is exactly one of the
payload-checked JSON renderers, without instance attributes, it renders
`data` as DRF's `rendered_content` does (the renderer's media type and charset,
or the explicit `content_type`; no `Content-Type` for an empty body) and sets
`renderer_context`; on the event loop when `marshal` recognizes the payload,
in a worker otherwise or when the view defines `get_renderer_context`. It then
drops `data` (releasing a returned serializer as `close()` does): under
concurrency, a payload kept until the response is closed survives collections
and is scanned again by the collector. Any
other renderer gets DRF's `Response`, built with the status, headers and
cookies of the `DataResponse`. `close()` releases the same back-references as
`Response.close()`.

## 6. Streaming responses and the lifespan

`aiodrf.response.StreamingResponse` renders items from an async iterable as
newline-delimited JSON, `StreamingArrayResponse` as one JSON array, and
`EventStreamResponse` as server-sent events (`ServerSentEvent` carries the
`event`, `id` and `retry` fields; a `keepalive` sends a comment line while
the iterable is quiet, from a task that waits without cancelling it; every
step of the iterable runs in one context, so context variables it sets
survive the waits). Items
are rendered on the loop after the payload check of section 5 (a lazy value or a
queryset sends that item to a thread) or, for a synchronous iterable, in the
request's thread `chunk_size` items per hop. Django's ASGI handler sends
each chunk as it is produced and cancels the iteration on `http.disconnect`,
and the response explicitly closes its owned producer, including a disconnect
while the transport is sending a chunk. A partially consumed response can be
closed with `await response.aclose()`. Synchronous iterator creation, reads
and cleanup stay in the thread-sensitive worker; cleanup costs one extra hop.
A generator expression is created where it is written: `(BookSerializer(b).data
for b in Book.objects.all())` calls `iter()` on the queryset on the event loop,
which Django refuses with `SynchronousOnlyOperation`. Pass a generator
function, `map(...)` over the queryset, or an async generator.
The response also closes the current async iterator installed by streaming
middleware. Middleware wrapping another resource-owning iterator must close
that iterator in its own `finally` block; `contextlib.aclosing` is suitable.
The NDJSON and JSON-array classes require a renderer with an `application/json`
or `application/*+json` media type. SSE data rendering is a separate contract.
`chunk_size` must be a positive integer and `keepalive` a finite positive number.
SSE metadata rejects CR/LF/NUL and invalid retry values; text preserves terminal
newlines and normalizes CR/CRLF to LF. Under WSGI Django consumes an async
iterator whole before answering, and warns. Use ASGI for live/infinite streams.

`aiodrf.asgi.get_asgi_application()` wraps Django's application in one that
answers the ASGI `lifespan` connection, which Django refuses: it sends
`aiodrf.signals.asgi_startup` and `asgi_shutdown` (`Signal.asend_robust`, so
receivers may be `async def`) and reports a receiver's exception as a
failed startup or shutdown with its traceback. Startup failure terminates the
lifespan connection without waiting for a shutdown message. On servers supporting
lifespan state, what a receiver puts in `scope["state"]` reaches every request as
`request.scope["state"]`; servers may omit state support. aiodrf's optional
resource context is separate: `AIODRF["LIFESPAN"]` accepts a zero-argument
async context manager factory or dotted path. The wrapper enters it before
startup signals, publishes its yielded resource under a namespaced ASGI state
key, and exits after shutdown signals, including failure and cancellation.
`get_lifespan_state(request, ResourceType)` checks both the live state and its
type. See the [lifespan guide](lifespan.md) for ownership and deployment limits.

Signals are notifications, not a resource manager. Robust dispatch waits for
every receiver before reporting an ordinary receiver exception; cancellation
still propagates. Receivers must not depend on one another's order, and a
receiver that succeeded cleans up after itself if another fails startup:
there may be no shutdown event afterwards. Put dependent resources in one
receiver or context manager when ordering and rollback matter.

Django's task framework and file uploads need nothing from aiodrf. `aenqueue()`
works from async views, and `ImmediateBackend` runs a synchronous task in the
request's thread and an `async def` task on the event loop. Multipart uploads to
a `FileField` are parsed and saved inside the action's hop, since Django reads the ASGI body
before the view runs and its storages are synchronous. A cancelled request
waiting for a synchronous save or task enqueue does not stop the running
worker; the operation may still commit or enqueue. For durable delivery use
the backend's idempotency or outbox mechanism rather than retrying a
cancelled write.

## 7. Transactions

`ATOMIC_SAVE` (default on) wraps the default synchronous save (`create` /
`update` with their many-to-many writes) in `transaction.atomic(using=...)`,
inside the hop, on the alias Django's routers choose for the write: with the
instance as a hint, as `Model.save()` routes, and for a list update of
instances loaded from several databases one `atomic` per alias (entered
together, not a two-phase commit). An unevaluated queryset given as the
instance is not evaluated for this (it would query outside the transaction);
its write goes to the model's alias. It does not cover `perform_create` as a
whole, an `acreate`/`aupdate` (async code is not wrapped in a synchronous
`atomic`; for a list, each child's), or a serializer that overrides `save()`.

A `save()` override owns its transaction policy. aiodrf
cannot see its transaction boundaries: it may write to another alias, call an
external service (holding a transaction and its locks open meanwhile),
register `on_commit` work, or keep partial work deliberately. Wrapping it
would silently change what the serializer does under DRF, and make the rule
"aiodrf wraps only the save it owns" depend on how a package is written.
Where the save must be atomic, say so in the code: `transaction.atomic()` in
`perform_create`/`perform_update` or in the override. For example,
drf-writable-nested keeps the parent when a child fails in its own save, as in
DRF, whereas an atomic `perform_create` rolls the parent back. Django refuses
`ATOMIC_REQUESTS` for async views (system check `aiodrf.W002`). On PostgreSQL
the transaction costs two round trips. After a failed statement the
connection remains usable, and `on_commit`, failing signal receivers and nested
`atomic()` blocks behave as in Django.

A backend whose `transaction.atomic()` is not a transaction registers the
context manager to enter instead, per database vendor, in the private
`aio._save._ATOMIC_FACTORIES`; it is entered when the save runs, in the
worker thread. django-mongodb-backend's is a no-op, and
`aiodrf.contrib.mongodb` registers the backend's own
([MongoDB guide](async-nosql.md#transactions)).

## 8. State

| State | Policy |
| --- | --- |
| `aiodrf_settings` | DRF-style lazy settings; values are validated before they are cached and published under a lock with a generation check, so a value read before a `reload()` is never cached after it |
| purity and bridge registries | filled at import time and in `AppConfig.ready()`; class membership and method registrations use weak class keys. Explicit callable registrations retain their callables. `is_pure` answers are kept per class (`class_cache`); every declaration (`async_safe`, `register_pure*`) and a change of `AIODRF`/`REST_FRAMEWORK` clears them (`utils._PureRegistry.changed`) |
| `utils.class_cache` (`resolve_pair`, `user_defines`, `is_declarative_class`, ...) | weak per class, with capacity eviction after 1024 publications per decorated function to bound value-to-key cycles; reads and computation occur outside the lock, publication and clearing inside it. A generation counter prevents a value computed before `cache_clear()` from being published after it. The answers describe a class as it was first used: a class whose members are assigned later (a test patching a method) needs the function's `cache_clear()` |
| where a request's steps run (`views._inline_checks`, `views._builds_throttles_inline`) | worked out once per view class and configuration: the classes a step reads from the view, as they are on the request, so an `as_view()` argument, an attribute set on the instance or changed on the class (a list changed in place too) is a configuration of its own; weak per view class (`class_cache`). What the view and the configured classes override is kept; purity is asked on every request, so a later declaration or `AIODRF` change counts at once. Bridge and transparent registrations belong at import time |
| content negotiation (`views._negotiations`) | only DRF's `DefaultContentNegotiation`, unchanged, for a view that overrides none of `perform_content_negotiation`, `get_renderers`, `get_content_negotiator`. Key: the `Accept` header, the format asked for (suffix or `URL_FORMAT_OVERRIDE`) and the renderers' media types and formats, read as DRF reads them; value: the renderer's position and the media type, so every request has its own renderers. A 406 or 404 is not kept, nor an `Accept` longer than 256 characters; at most 1024 entries, emptied when full |
| synchronous-hook check (`hooks.require_sync_hooks`) | what a parser's or renderer's class defines is checked once per class (`class_cache`); a hook set on the instance is checked on every use |
| `aio._classify._CLASS_KINDS` | serializers whose fields are a function of their class (`_classify.is_static`: DRF's declared fields and children, no field-building hooks, `Meta.depth` or model fields that call the project's code, usual arguments, nothing shadowed on the instance, nested serializers static too) and whose validators were not materialized; any other, or an instance whose fields were built (and perhaps edited, a nested serializer's included), is classified per instance; cleared on `setting_changed` |
| compiled output and input variants | at most 1024 weak class buckets per cache and 32 dynamic variants per class; class and variant bounds enforced under their publication locks. Clearing detaches old buckets; `REST_FRAMEWORK` changes invalidate format-dependent entries |
| schema serializers (per schema, and per input/output schema pair and model), partial schemas, list adapters | `contrib.typed.BoundedCache`, `SCHEMA_CACHE_SIZE` (1024) each: strong references, hits without a lock, built once per key under a lock, second-chance eviction |
| a view's static serializer | resolved and checked against `ALLOWED_SERIALIZER_BACKENDS` when the URL is built (`APIView._compile_serializers`, `aiodrf.backends`); a class chosen per request is checked in `get_serializer` |
| `contrib.builtin.prefetch._lookup_cache` | one owner for weak serializer/model keys, path values and the publication lock. Reads are unlocked; clearing replaces the table and snapshot identity rejects stale publication. `Meta.prefetch` is read from each request's serializer; request querysets are not cached |
| hop counter | an explicit block owns the counter and closes recording in `finally`; the `ContextVar` holds a weak reference. Recording and closing share a lock; `count` remains `len(calls)` |

The [state-ownership reference](../architecture/state-ownership.md) explains
retention limits, application-startup registrations and memory regression tests.

The package is typed (`py.typed` is shipped): every function is annotated, and
it is checked with mypy, django-stubs and djangorestframework-stubs, both on its
own code and from the point of view of code that uses it. Members that override DRF's keep the types of DRF's stubs,
except that aiodrf's handlers are coroutine functions where DRF's are
synchronous.

The whole test suite also runs on CPython 3.14 without the GIL, including tests
that populate aiodrf's caches from many threads at once. Django 6.1 makes no
statement about free-threaded builds, so the package's classifier marks this
support as beta.

## 9. Compiled serializers

See the [serializer guide](msgspec-pydantic.md). Output: one analysis
(`contrib/compiler.py`) of the DRF serializer, turned into a msgspec Struct, a
pydantic model or plain Python readers (`contrib/builtin/output.py`, the
dependency-free `"python"` backend) when the result is known to equal DRF's;
DRF otherwise (`SERIALIZER_BACKEND_FALLBACK = "error"` raises instead for a
serializer that cannot be compiled). An instance the compiled output cannot
read is represented by DRF whatever the fallback. Input (`contrib/inputs.py`,
msgspec or pydantic; the python backend leaves input to DRF): a recognizer that
accepts canonical input for which DRF would produce the same
`validated_data`; on any rejection DRF validates and reports. A serializer
whose fields are a function of its class (`_classify.is_static`) is answered
once per class and per partial/validators/instance-hook state, without
building its fields for each request; any other is looked up by the signature
of its fields. It is verified against DRF with generated input.

## 10. Implementation choices

| Component | Current design | Constraint |
| --- | --- | --- |
| Inline rendering | Check supported payload types before encoding (`marshal` for built-in types) | Unknown callbacks select the worker without exception-driven replay |
| Request plan | Decide a default lifecycle once per class; check the configuration per request | Any override, instance setting or changed class list keeps the generic path |
| Closed responses | Cut the request objects' back-references in `close()` | Keep `data` and the context's view and request readable |
| Pair-cache publication | Compute outside the publication lock; use a generation on invalidation | Duplicate deterministic computation is permitted; stale results cannot be republished |
| Schema caches | Bounded second-chance caches with active-class identity protection | Limit runtime-generated schema growth without changing an active adapter's identity |
| Migration tooling | Track class decisions and module imports | Leave ambiguous inheritance/imports unchanged for application review |
| Authentication and actions | Preserve separate lifecycle extension points | Do not reorder user hooks to remove a worker transition |
| Classification | Cache only supported static declarations | Custom field-building hooks and instance mutations require per-instance inspection |

## 11. Conditional requests, query parameters and QUERY

`get_etag` / `aget_etag` and `get_last_modified` / `aget_last_modified` are
evaluated in `dispatch` after `initial` (negotiation, authentication,
permissions, throttles) and before the handler, with Django's
`get_conditional_response`. A view that defines neither pays nothing; async
or `@async_safe` hooks run on the loop; synchronous ones share one worker hop.
A 304 skips the handler; a failed precondition raises
`aiodrf.exceptions.PreconditionFailed` (412). See the
[extension-hook contract](extension-hooks.md#conditional-requests).

`query_serializer_class` validates `request.query_params` on request
(`get_validated_query_params` / `aget_validated_query_params`): on the loop
for a declarative serializer, in one worker hop together with a project's
factory or a serializer with code of its own.

The HTTP QUERY method (RFC 10008) is a safe, idempotent request whose content
is the query. Django 5.2 to 6.1 do not dispatch it; `aiodrf.compat.DJANGO_HAS_QUERY`
records whether Django's `View` does, and while it does not, aiodrf's
`APIView.http_method_names` adds `"query"`, marked
`TODO(django#37232)` in the code, so the addition can be removed with the
Django versions that lack it (ticket #37232, PR django/django#21855). Nothing
else of Django's or DRF's is changed. A view answers QUERY when it defines a
`query` handler (or a viewset maps an `@action(methods=["query"])`); others
answer 405 without QUERY in `Allow`. The behaviour, by section of RFC 10008:

- Sections 2, 2.1: before the handler runs, a QUERY without a `Content-Type` or
  without content is a 400, content that does not parse as its media type a
  400, a media type no parser accepts a 415 with `Accept-Query`. Semantic
  errors in the query are DRF's `ValidationError`s, 400, where the RFC
  suggests 422; aiodrf does not change DRF's status. An unacceptable
  `Accept` is DRF's 406.
- Section 2.6: conditional QUERY requests are evaluated like a GET (304, 412), with
  a copy of the request whose method is GET until Django evaluates QUERY
  itself; the hooks may read the content, which is part of the query.
- Section 3: `OPTIONS` on a view with a QUERY handler lists it in `Allow` and sends
  `Accept-Query`, the parsers' media types as a Structured Fields list
  (`aiodrf.views.accept_query`).
- Test clients: `AsyncAPIClient.query()` and `AsyncAPIRequestFactory.query()`;
  DRF's synchronous client reaches QUERY with `generic("QUERY", ...)`.

Left to Django, DRF and the project, deliberately:

- DRF's `SAFE_METHODS` do not include QUERY, so `IsAuthenticatedOrReadOnly`
  refuses an anonymous QUERY and `DjangoModelPermissions` answers 405 (its
  `perms_map` has no entry; add `"QUERY": []` or the permissions a query
  needs in a subclass).
- Django's CSRF middleware exempts only GET, HEAD, OPTIONS and TRACE, so a
  QUERY with session authentication needs the CSRF token, like a POST.
- Caching: the RFC's cache key includes the content (section 2.7); Django's
  `cache_page` keys on the URL, so do not cache QUERY responses with it.
- HEAD is not derived from QUERY.
- CORS: browsers preflight QUERY (section 4); django-cors-headers allows it only
  when `CORS_ALLOW_METHODS` lists it.
- OpenAPI 3.0 and 3.1 have no QUERY operation, and drf-spectacular fails on
  a view that implements one; the opt-in preprocessing hook
  `aiodrf.contrib.spectacular.hooks.preprocess_exclude_query_method` leaves
  the QUERY operations out and documents the rest.
