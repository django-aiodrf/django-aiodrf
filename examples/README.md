# aiodrf examples

Independent Django projects using the local aiodrf checkout. Each directory
contains a uv project, its own settings/ASGI application, runnable requests,
tests and an evaluation of the demonstrated features. No example imports
another example's application.

## Catalogue

| Project | Focus | Default port | External service |
| --- | --- | ---: | --- |
| [basics](basics/README.md) | APIView, decorators, async field validation, serializer factories, exception/finalize hooks and an unchanged synchronous DRF view; Uvicorn and optional Granian commands. | 8101 | None |
| [streaming](streaming/README.md) | StreamingResponse, StreamingArrayResponse, EventStreamResponse, keepalive and deterministic generator cleanup; item OpenAPI annotations. | 8102 | None |
| [lifespan](lifespan/README.md) | Config-path async context manager, typed request state, deterministic HTTP client shutdown and an AsyncCommand. | 8103 | None |
| [list-enrichment](list-enrichment/README.md) | PrefetchListSerializer for one batch per list; ConcurrentListSerializer for independent item I/O with separate item contexts and stable ordering. | 8104 | None |
| [typed-schemas](typed-schemas/README.md) | Native msgspec/Pydantic schemas, context, custom types, root output, PATCH, query validation and Swagger. | 8105 | None |
| [serializer-backends](serializer-backends/README.md) | The same DRF model fields under normal, strict-msgspec, strict-pydantic and tuned profiles; per-serializer override, field-template caching, relation batching and explicit read/write serialization. | 8106 | None |
| [crud](crud/README.md) | ModelViewSet, normal DRF router/actions, django-filter, search/ordering, page/limit-offset/cursor pagination and PATCH. | 8107 | None |
| [tasks-django5](tasks-django5/README.md) | django-tasks backport using its own imports/settings, sync and async jobs and a dummy queue. | 8110 | None |
| [tasks-django6](tasks-django6/README.md) | Django's built-in Tasks API with aenqueue, immediate jobs and a dummy queue. | 8111 | None |
| [policies](policies/README.md) | Token/Basic/session authentication, CSRF middleware, async authentication challenges, composed permissions, fixed-window throttling, public cache_page, ETags and explicit QUERY handling. | 8108 | None |
| [uploads](uploads/README.md) | Multipart request parsing, validation, size limits and explicit storage I/O in a thread-sensitive worker. | 8109 | None |
| [telemetry](telemetry/README.md) | Opt-in request phase spans, an application-owned tracer provider, console exporter and lifespan shutdown. | 8112 | None |
| [migration](migration/README.md) | Opt-in ADRF import shim, legacy async property and a non-mutating codemod preview. | 8113 | None |
| [middleware-experiment](middleware-experiment/README.md) | Standard Django middleware by default; opt-in grouped synchronous subclasses in a separate process profile. | 8114 | None |
| [native-postgres](native-postgres/README.md) | Explicit native ModelSerializer/ModelViewSet, async_objects and the backend-owned connection lifecycle. | 8115 | PostgreSQL |
| [mongodb](mongodb/README.md) | Django MongoDB models and native async PyMongo read/write paths, ObjectId representation and ORM transaction adapter. | 8116 | MongoDB replica set |
| [vendor-authentication](vendor-authentication/README.md) | Vendor authentication classes on aiodrf views; opt-in missing-credential checks for SimpleJWT, Knox and drf-auth-kit. | 8117 | None |
| [bookshop](bookshop/README.md) | Combined CRUD, token auth, filtering, batching, typed lifespan, streaming and schema fuzz tests | 8118 | None by default; optional stock service |
| [django-builtin](django-builtin/README.md) | Admin, auth/session/CSRF, templates, forms, mail, signals, database defaults, transactions and all Django cache backend configurations. | 8119 | Optional Redis/Memcached |
| [ecosystem-security](ecosystem-security/README.md) | Guardian and rules permissions, filtering, history/audit, OAuth scopes, Djoser, allauth headless, dj-rest-auth cookies, CORS and Swagger. | 8120 | None |
| [ecosystem-data](ecosystem-data/README.md) | rest-filters, vendor fields, nested writes, polymorphism, soft deletion, cleanup, dataclasses, nested routers, JSON:API, XLSX, DataTables and browsable API. | 8121 | None |
| [ecosystem-platform](ecosystem-platform/README.md) | Separately tested logging, lockout/idempotency, debugging, profiling, metrics, query caching, DI, WebSockets, health checks and typed commands. | 8122 | Optional Redis or telemetry credentials |
| [ecosystem-services](ecosystem-services/README.md) | Celery, storage/S3, Elasticsearch and OpenSearch, native Redis/Valkey cache and middleware, Sentry and telemetry. | 8123 | None by default; optional broker/storage/search/cache services |
| [tenancy](tenancy/README.md) | django-tenants engine/router/middleware, schema-scoped CRUD and concurrent ASGI tenant isolation. | 8124 | PostgreSQL |

## Run one project

Every project also has a Docker Compose service with an isolated Python
environment. From the repository root:

```console
docker compose -f examples/compose.yaml up --build --wait basics
```

See [container setup](CONTAINERS.md) for ports, database services, cache/search
profiles, tests and data retention. The host-based uv commands below remain
available; Docker is not a package dependency.

```console
cd examples/basics
uv venv
uv pip install --python .venv/bin/python -r pyproject.toml --group test -e ../..
uv run --no-sync python manage.py check
uv run --no-sync uvicorn project.asgi:application --host 127.0.0.1 --port 8101
```

Each project pins the interpreter family in `.python-version`; Python 3.12
is the common example baseline. uv manages its own `.venv`. Dependencies are resolved from each project's `pyproject.toml`; no lock file is
committed. The explicit editable install (`-e ../..`) selects this checkout; no
PyPI release is required. Use `uv run --no-sync` after installation.

The shown interpreter path is for POSIX shells. On Windows use
`.venv/Scripts/python.exe`. The catalogue runner selects the appropriate path
automatically and isolates each example from a parent virtual environment.

For database examples run `uv run --no-sync python manage.py migrate` before the server.
Tests use isolated test databases. Configure a dedicated database/role for
PostgreSQL or MongoDB; never point these examples at production data.

Uvicorn accepts `--host` and `--port` in every project. For another hostname
set `EXAMPLE_ALLOWED_HOSTS` too. Default listeners are loopback. Endpoints
are deliberately simple local demonstrations, not production security settings.

## Verify the catalogue

From the repository root, with uv installed:

```console
python3 examples/check.py
python3 examples/check.py basics serializer-backends tasks-django5
python3 examples/check.py native-postgres mongodb --include-services
```

The runner creates/synchronizes each project's own environment, runs Django
checks and its ASGI tests. External database projects are explicitly reported
as skipped unless selected with `--include-services`; unavailable selected
services fail the run. It does not start or stop shared services or run long
load tests. `--check-only` omits pytest.

The [feature evaluation matrix](FEATURES.md) covers the public capability
groups and every aiodrf setting, with default behavior, opt-ins and limits.
The [ecosystem inventory](ECOSYSTEM.md) maps every package in the compatibility
session to its runnable project, optional profile or executable service recipe.
The [measurement guide](../docs/guides/performance.md) separates functional
contracts from workload-specific performance claims.

## Choosing a profile

Start with `basics` or `crud`, then select the integration you need.
`serializer-backends` exposes normal, strict-msgspec, strict-pydantic and tuned
profiles over the same model declarations. `middleware-experiment` starts
with standard Django middleware; grouped mode requires an explicit environment
setting and a process restart. `migration` is an isolated, opt-in import shim
for applications migrating from ADRF.

Third-party package compatibility tests are inventoried in the
[ecosystem guide](../docs/guides/ecosystem.md). A compatibility test is not a
package-specific production application. In particular, the examples do not
claim to configure production OAuth, multi-tenancy, S3, Celery or Elasticsearch
clusters; use their existing integration suites and deployment requirements.
