# Feature evaluation and example coverage

This page maps aiodrf's features and settings to the examples that demonstrate
them. The examples do not exercise every DRF field or every combination of
settings. Each example's README describes what it runs and its limits; the
[catalogue](README.md) lists the commands.

## Public capabilities

| Capability | Runnable example | Evaluation and regression boundary |
| --- | --- | --- |
| Async APIView, decorators, normal HTTP methods, DRF coexistence | [basics](basics/README.md) | Retains DRF request/response shape. Sync third-party code remains worker work. Async is not a CPU speedup. |
| Generic views, mixins, viewsets, routers, actions and CRUD | [crud](crud/README.md), [bookshop](bookshop/README.md) | Normal action names; sync/async hooks resolve by ownership. Write transaction behavior is documented separately from native transactions. |
| Serializer factories, async field validation, context and response hooks | [basics](basics/README.md) | Explicit async counterparts; arbitrary DRF methods are not automatically awaitable. Complete pair table: [extension hooks](../docs/guides/extension-hooks.md). |
| Model serializers, relation input, partial updates and nested output | [serializer-backends](serializer-backends/README.md), [crud](crud/README.md) | DRF fields/validators retained; related-manager output is re-read after writes. No shared bound field objects. |
| msgspec/Pydantic/python compilation of DRF declarations | [serializer-backends](serializer-backends/README.md) | Whole-serializer eligibility, strict default, documented fast parity, DRF/error fallback. Inspect eligibility rather than assuming every class compiles. |
| Per-class and global compiler selection; inspection command | [serializer-backends](serializer-backends/README.md) | Main `.data` remains DRF's synchronous API. Use awaited operations to activate compilation. |
| Typed schema serializers, partial input, codecs and backend allowlist | [typed-schemas](typed-schemas/README.md) | A different schema contract from compiling DRF declarations; backend-specific error mapping and partial-schema rules apply. |
| OpenAPI, Swagger, query parameters and stream item annotations | [typed-schemas](typed-schemas/README.md), [streaming](streaming/README.md), [bookshop](bookshop/README.md) | Schema generation must work offline, without lifespan I/O. QUERY cannot be emitted as an OpenAPI 3.0/3.1 operation; the guide supplies the exclusion hook. |
| django-filter, ordering/search and three paginator types | [crud](crud/README.md) | Query construction and customized filters run in the worker. Counts and prefetches remain real queries. |
| ORM prefetch inference and explicit prefetches | [bookshop](bookshop/README.md), [serializer-backends](serializer-backends/README.md) | `Meta.auto_prefetch` assists static sources; method fields still need explicit queryset design. It cannot infer arbitrary application code. |
| FETCH_MODE peers/raise | [crud](crud/README.md) | Django 6.1-only opt-in; `raise` diagnoses lazy reads, `peers` may load additional rows. A system-check error (`fastdrf.E006`) on older Django: leave it unset. |
| Batched list enrichment | [list-enrichment](list-enrichment/README.md), [bookshop](bookshop/README.md) | One async batch before representation is preferable to N independent service calls when the service supports batching. |
| Bounded concurrent item representation | [list-enrichment](list-enrichment/README.md) | Experimental; independent fresh serializers, stable order, bounded tasks. Not a parallel-ORM transaction API. Cancellation contracts have separate regression tests. |
| Session/Basic/Token auth, challenges, permissions and CSRF | [policies](policies/README.md) | Vendor semantics and Django middleware retained. Anonymous access, 401/403 and authenticated writes must be tested independently. |
| SimpleJWT, Knox and auth-kit credential shortcuts | [vendor-authentication](vendor-authentication/README.md) | Opt-in app registrations, not replacements for token verification. Missing credentials can avoid work; present credentials still invoke the vendor. |
| Fixed-window throttles and cache_page | [policies](policies/README.md), [bookshop](bookshop/README.md) | Per-process LocMem behavior; remote atomic counters depend on backend guarantees. Public caching is not a private-data cache design. |
| ETag/Last-Modified and preconditions | [policies](policies/README.md), [bookshop](bookshop/README.md) | Permission checks precede conditional response handling. If-Match does not make the later database write atomic. Last-Modified uses the same hook contract. |
| Query serializers and QUERY method | [typed-schemas](typed-schemas/README.md), [policies](policies/README.md) | Explicit handlers and validated input. Proxy, CSRF, permissions and OpenAPI compatibility are independent concerns. |
| NDJSON, JSON-array streaming, SSE and heartbeat | [streaming](streaming/README.md), [bookshop](bookshop/README.md) | ASGI iterators own cleanup; finite demo streams do not certify slow-client/backpressure behavior. Real disconnect tests are in the library/deployment suites. |
| Typed lifespan/context manager and config path | [lifespan](lifespan/README.md) | ASGI process lifetime, not request-global mutable state. No resources created by `AppConfig.ready()`. |
| AsyncCommand, command-owned resource lifetime | [lifespan](lifespan/README.md) | Django owns command parsing and connection cleanup. Signal/cancellation and `acall_command` regression cases live in the command test suite. |
| Django 5 task backport and Django 6 Tasks | [tasks-django5](tasks-django5/README.md), [tasks-django6](tasks-django6/README.md) | Direct upstream API, no wrapper. Immediate/Dummy are not production queue workers. |
| Multipart uploads/storage | [uploads](uploads/README.md) | Storage I/O stays off-loop. Real S3 credentials/bucket policy are not validated by a local filesystem example or SDK stubs. |
| OpenTelemetry phases | [telemetry](telemetry/README.md) | Provider, sampler and exporter belong to the application. Exported unexpected exception text requires an application privacy policy. |
| Native PostgreSQL adapter | [native-postgres](native-postgres/README.md) | Explicit dependency/app/classes. Dependency patches Model; its native connection and sync signal connection are not one transaction. |
| MongoDB adapter and ObjectId | [mongodb](mongodb/README.md) | Version-matched backend, sync-driver worker boundary; replica set required for transactional guarantees. No native-async claim. |
| ADRF migration shim/codemod | [migration](migration/README.md) | Explicit sys.modules aliases, isolated from installed adrf. Codemod diff is reviewable and non-mutating. |
| Middleware scheduling experiment | [middleware-experiment](middleware-experiment/README.md) | Disabled by default; retained worker can hurt concurrency. No patch to stock Django middleware. |
| Test clients, request factories and hop diagnostics | Example `tests/` plus library tests | Examples use real ASGI HTTPX transport; `aiodrf.test` adds DRF-shaped clients/factories and `count_hops()`. A hop count is not elapsed time. |
| System checks and async safety declarations | Every project's `manage.py check` | Unsupported combinations fail/warn at startup. `async_safe` is an application assertion, not automatic detection of blocking I/O. |

## Every aiodrf setting

Settings not shown in a project's override retain the library default. No demo
silently enables an experimental option. The authoritative defaults remain in
[`aiodrf.settings`](../src/aiodrf/settings.py) and the root README's tested table.

| Setting | Default/evaluation | Example or explicit exclusion |
| --- | --- | --- |
| `LIFESPAN` | None; application-owned async context manager | lifespan, telemetry, bookshop |
| `UNSAFE_SYNC_MIDDLEWARE` | False; process-start choice, retained-thread tradeoff | middleware-experiment only |
| `VALIDATION_UNKNOWN` | thread; inline asserts all custom validation is nonblocking | Kept safe in every example; do not switch globally to hide a slow validator |
| `REPRESENTATION_MODE` | thread; inline requires loaded, nonblocking sources | Kept safe; compilers have their own eligibility checks |
| `ATOMIC_SAVE` | True for owned default sync save units | CRUD; native-postgres uses its native transaction adapter; MongoDB uses its backend adapter |
| `INLINE_RENDERERS` | Empty; custom renderers must be CPU-only to opt in | typed-schemas uses the supported msgspec codec, not a wildcard purity declaration |
| `PURE_POLICIES` | Empty; a declaration never validates the policy's implementation | Deliberately not used for authentication SDKs in vendor-authentication |
| `ADRF_COMPAT` | False; aliases only with explicit migration opt-in | migration environments only |
| `MONKEYPATCHES` | Empty; named, process-wide patches of DRF classes | not used by the examples |
| `REQUEST_THREADS` | None; Django's two thread starts per request | not used by the examples; see the [tuned profile](../docs/guides/tuned-profile.md#request-threads) |

### django-fastdrf settings (`FASTDRF`)

The serializer optimizations are django-fastdrf's, a dependency of aiodrf; their
defaults live in `fastdrf.settings` and the
[settings reference](../docs/reference/settings.md#django-fastdrf-settings-fastdrf).

| Setting | Default/evaluation | Example or explicit exclusion |
| --- | --- | --- |
| `FETCH_MODE` | None; peers/raise require Django 6.1, a system-check error (`fastdrf.E006`) before | crud environment option |
| `SERIALIZER_BACKEND` | drf | serializer-backends, global and per-class endpoints |
| `SERIALIZER_BACKEND_PARITY` | strict | strict-msgspec, strict-pydantic and explicit tuned/fast profile |
| `SERIALIZER_BACKEND_FALLBACK` | drf; error is useful to enforce expected eligibility in tests | serializer inspection; do not promise acceleration after fallback |
| `ALLOWED_SERIALIZER_BACKENDS` | drf/msgspec/pydantic | typed-schemas; deployment may narrow this list |
| `CACHE_SERIALIZER_FIELDS` | False; only static classes, independent field copies | tuned profile |
| `FIELD_COPY_MODE` | deepcopy; clone/compiled require field caching | tuned-clone and tuned-compiled profiles; serializer/view selection; recursive constructors and custom-copy fallback |
| `BATCH_RELATED_LOOKUPS` | False; narrow PK relation-input batching | tuned profile, preserving invalid-key errors and write semantics |

## Beyond the examples

The [ecosystem guide](../docs/guides/ecosystem.md) describes how aiodrf works
with third-party packages such as OAuth providers, django-tenants, Channels,
Redis, S3, Celery, Elasticsearch, audit and history packages, caches and nested
serializers. Deployment capacity, real S3 and telemetry destinations, durable
task delivery and authorization of private APIs have to be verified in your own
environment.
