# Django and ecosystem example coverage

This page shows which example application demonstrates each Django feature and
third-party package, and what the example exercises. Each project's README
explains its setup, host and port options, requests and tests. aiodrf's own
features are mapped in the [feature matrix](FEATURES.md), and the tested
behaviour of each package is described in the
[ecosystem guide](../docs/guides/ecosystem.md).

## Django services

| Area | Project | Exercised boundary |
| --- | --- | --- |
| ORM, routers, serializers, filtering, pagination | [CRUD](crud/README.md) | SQLite CRUD, validation, relation loading, actions and pagination through ASGI |
| Admin, authentication URLs, sessions, CSRF, messages, locale | [Built-ins](django-builtin/README.md) | Standard middleware and authentication routes; admin registration, not an exhaustive admin UI test |
| Templates, humanize, sites, flatpages, redirects, sitemaps, feeds | [Built-ins](django-builtin/README.md) | Django owns rendering and content endpoints beside async API views |
| Forms, mail, signals, database defaults, generated fields, on_commit | [Built-ins](django-builtin/README.md) | Form errors, local mail, async signal dispatch and persisted computed values |
| LocMemCache, DummyCache, FileBasedCache, DatabaseCache | [Built-ins](django-builtin/README.md) | Real backend read/write contracts without a service |
| RedisCache, PyMemcacheCache, PyLibMCCache | [Built-ins](django-builtin/README.md) | Explicit service profiles; remote tests opt in and never flush a shared cache |
| Storage and uploaded files | [Uploads](uploads/README.md), [services](ecosystem-services/README.md) | Validation and blocking storage work in a worker, request/file ownership |
| Streaming and client disconnect | [Streaming](streaming/README.md) | Async generator cleanup; larger cancellation/resource suites remain in tests |
| Django 6 Tasks and Django 5 backport | [Django 6 tasks](tasks-django6/README.md), [Django 5 tasks](tasks-django5/README.md) | Native/backport imports and settings, enqueue/result contracts |
| PostgreSQL and native async backend | [Native PostgreSQL](native-postgres/README.md), [tenancy](tenancy/README.md) | Dedicated database required; ordinary PostgreSQL locking also has a separate repository test session |

The examples do not cover PostGIS, every admin widget, every database engine or
a production deployment.

## Authentication and authorization

| Packages | Project | Exercised boundary |
| --- | --- | --- |
| `djangorestframework-simplejwt`, `django-rest-knox`, `drf-auth-kit` | [Vendor authentication](vendor-authentication/README.md) | Vendor classes and explicit missing-credential fast paths |
| `django-oauth-toolkit` | [Security](ecosystem-security/README.md) | OAuth token expiry and scope checks |
| `django-guardian`, `djangorestframework-guardian`, `rules` | [Security](ecosystem-security/README.md) | Object-filtered lists, denied object access and ownership rules |
| `djoser`, `django-allauth`, `dj-rest-auth` | [Security](ecosystem-security/README.md) | Token login, headless authentication and JWT cookies; no external identity provider |
| `django-cors-headers`, `django-simple-history`, `django-auditlog` | [Security](ecosystem-security/README.md) | Preflight, history, audit actor and masked values |
| `django-axes`, `django-idempotency-key` | [Platform](ecosystem-platform/README.md) protection profile | Lockout and vendor replay semantics |

## Serialization, models and representations

| Packages | Project | Exercised boundary |
| --- | --- | --- |
| `drf-writable-nested`, `drf-flex-fields` | [Data](ecosystem-data/README.md) | Nested writes and explicit expansion |
| `django-polymorphic`, `django-rest-polymorphic`, `django-safedelete` | [Data](ecosystem-data/README.md) | Subclass output and soft-delete visibility |
| `django-money`, `django-phonenumber-field`, `django-taggit` | [Data](ecosystem-data/README.md) | Money, phone and tag fields through ordinary DRF declarations |
| `drf-extra-fields`, `django-pydantic-field`, `djangorestframework-dataclasses` | [Data](ecosystem-data/README.md) | Image validation, model schema fields and dataclass input/output |
| `django-cleanup` | [Data](ecosystem-data/README.md) | Storage cleanup on transaction commit |
| `drf-nested-routers`, `nested-multipart-parser` | [Data](ecosystem-data/README.md) | Parent-scoped writes and nested multipart input |
| `drf-orjson-renderer`, `drf-excel`, `djangorestframework-camel-case` | [Data](ecosystem-data/README.md) | Vendor JSON, spreadsheet output and key transformation |
| `djangorestframework-jsonapi`, `djangorestframework-datatables` | [Data](ecosystem-data/README.md) | Read-only JSON:API and DataTables protocol |
| `drf-tweaks`, `drf-restwind`, `drf-standardized-errors` | [Data](ecosystem-data/README.md) | Pagination, browsable HTML and error envelopes |
| `django-restql` | [Data](ecosystem-data/README.md) isolated extra/profile | Projection query; pyPEG2 cold-import warnings asserted explicitly |
| `django-filter`, `drf-spectacular` | [CRUD](crud/README.md), [typed schemas](typed-schemas/README.md) | Filtering and OpenAPI generation, including async/typed extensions |
| `rest-filters` | [Data](ecosystem-data/README.md) | Validated query parameters, relation lookups and list/detail filtering; DRF/spectacular parity is covered in the ecosystem suite |
| `msgspec`, `pydantic` | [Serializer backends](serializer-backends/README.md), [typed schemas](typed-schemas/README.md) | Strict compiler profiles, explicit native schemas and opt-in tuning |

## Operations and external services

| Packages | Project | Exercised boundary |
| --- | --- | --- |
| `granian`, `uvicorn` | [Basics](basics/README.md), [server guide](../docs/guides/web-servers.md) | Separate socket tests cover HTTP, DB, lifespan and SSE cleanup; server pins live in the dedicated requirements profile |
| `asgi-lifespan`, `httpx` | [Lifespan](lifespan/README.md), all ASGI test fixtures | Startup/shutdown ownership and HTTP requests without a network listener |
| `django-redis` | [Built-ins](django-builtin/README.md) | Vendor cache backend, explicit remote service test |
| `django-cachalot`, `django-zeal`, `django-cacheops` | [Platform](ecosystem-platform/README.md) cache profiles | ORM caching/N+1 tooling; cacheops requires Redis |
| `channels`, `djangochannelsrestframework` | [Platform](ecosystem-platform/README.md) | WebSocket permission and list action; separate HTTP application |
| `django-health-check`, `whitenoise`, `servestatic`, `django-typer` | [Platform](ecosystem-platform/README.md) | Health endpoint, ASGI/WSGI static-file profiles and typed command |
| `django-structlog`, `django-log-request-id`, `drf-api-logger` | [Platform](ecosystem-platform/README.md) logging profile | Request context and vendor logging signal, no remote exporter |
| `django-debug-toolbar`, `django-silk`, `django-prometheus` | [Platform](ecosystem-platform/README.md) separate profiles | Diagnostics, SQL profiling and metrics; restricted/local interfaces |
| `wireup`, `apitally` | [Platform](ecosystem-platform/README.md) explicit profiles | Constructor injection; Apitally needs credentials to send data |
| `django-elasticsearch-dsl`, `elasticsearch` | [Services](ecosystem-services/README.md) | Document preparation locally; native search/index commands require a dedicated Elasticsearch service |
| `django-opensearch-dsl`, `opensearch-py` | [Services](ecosystem-services/README.md#opensearch) | Separate document registry, worker preparation and native HTTP; optional Docker overlay |
| Native Redis/Valkey contrib backends and page-cache middleware | [Services](ecosystem-services/README.md#native-cache-middleware) | Shared lifespan pools, explicit callback policy, Django Vary/HEAD cache semantics |
| `django-valkey` | [Services](ecosystem-services/README.md) | Native async reads/writes with lifespan-owned pools |
| `celery`, `django-storages`, `sentry-sdk` | [Services](ecosystem-services/README.md) | Commit-aware jobs, storage and error-reporting lifecycle; local defaults contact no cloud service |
| `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-instrumentation-django`, `opentelemetry-instrumentation-asgi` | [Telemetry](telemetry/README.md), [services](ecosystem-services/README.md) | Application spans and executable offline Django/ASGI instrumentation recipes |
| `django-tenants` | [Tenancy](tenancy/README.md) | Real schema routing and concurrent tenant isolation on PostgreSQL |
| `django-mongodb-backend` | [MongoDB](mongodb/README.md) | ObjectId fields and explicit backend transaction adapter; replica set required |
| `django-async-backend` | [Native PostgreSQL](native-postgres/README.md) | Opt-in native views/serializers and backend-owned connections |
| `django-tasks` | [Django 5 tasks](tasks-django5/README.md) | Backport API, not django-background-tasks |

## Notes

- django-restql depends on pyPEG2, which emits an import warning; the example
  keeps it in a separate profile.
- WhiteNoise serves files synchronously, so under Uvicorn Django buffers its
  responses and warns. Serve static files with Nginx or a CDN in production,
  and do not silence the warning.
- Instrumentation installed by third-party ORM packages belongs to those
  packages; aiodrf does not patch any of them.
- Services such as S3, real telemetry exporters and cloud credentials require
  your own environment. See [deployment behaviour](../docs/guides/deployment-validation.md).
