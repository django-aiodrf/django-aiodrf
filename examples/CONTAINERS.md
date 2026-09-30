# Running examples with Docker

Every project in the [catalogue](README.md) has a service in the shared
Compose configuration. Each image resolves only that project's dependencies
and installs this checkout as `django-aiodrf`. No published package or host
Python environment is required.

These containers are local development fixtures. They use demonstration
secrets, public write endpoints and isolated, unauthenticated infrastructure.
They are not deployment templates.

## Start one example

Install Docker Engine with Compose v2. From the repository root:

```console
docker compose -f examples/compose.yaml up --build --wait basics
curl http://127.0.0.1:8101/echo/
docker compose -f examples/compose.yaml logs basics
docker compose -f examples/compose.yaml stop basics
```

Replace `basics` with any project directory from the catalogue. Each example's
README includes its service and requests. An explicit service starts only that
application and its declared dependencies. Do not enable the entire
`examples` profile unless you intend to run all projects.

The app runs as an unprivileged user. Its entrypoint runs Django migrations,
then replaces itself with Uvicorn. ASGI lifespan startup and shutdown are
enabled. The container health check detects a listening server; use the
example's HTTP requests and tests to verify application behavior.

The server listens on port 8000 inside its container. Published ports bind
only to the host's loopback address and default to the catalogue's 8101–8124
ports. To choose another host port for one selected example:

```console
EXAMPLE_PORT=8201 docker compose -f examples/compose.yaml up --build --wait basics
```

Do not set one `EXAMPLE_PORT` while starting several applications: they would
compete for that port. `EXAMPLE_ALLOWED_HOSTS` changes Django's accepted
hostnames, not the host listener. Publishing beyond loopback requires a
deliberate Compose override and an application security review.

## Commands and tests

Run a management command or tests in a running container:

```console
docker compose -f examples/compose.yaml exec basics python manage.py check
docker compose -f examples/compose.yaml exec basics pytest -q
docker compose -f examples/compose.yaml exec basics uv pip list --python /opt/venv/bin/python
```

For a one-off command, use `run --rm basics python manage.py check`.
The supplied command replaces normal startup; it does not implicitly migrate
the database. `exec` uses the already running application environment.

Use the individual README for credentials, fixture creation and optional tests.
A container that starts is not a tested one: run the example's tests as well.
Run service tests only against these disposable examples,
never a production database or bucket.

## Database and service examples

| Example | Started dependencies | Data ownership |
| --- | --- | --- |
| `native-postgres` | Dedicated PostgreSQL 18 instance | `native-postgres-data` volume |
| `tenancy` | Separate PostgreSQL 18 instance | `tenancy-data` volume |
| `mongodb` | MongoDB and one-shot replica-set initialization | `mongodb-data` volume |
| `ecosystem-services` | Elasticsearch and Valkey | Search/cache volumes and shared SQLite volume |
| Other projects | None by default | SQLite/media in the individual container, if used |

Infrastructure ports are not published on the host. Container DNS names are
configured in Compose; do not substitute `localhost` for another container.

PostgreSQL uses `example` as the database and role, with a local-only password.
The two projects do not share an instance. PostgreSQL 18 data mounts use
`/var/lib/postgresql`. Connections and migrations remain owned by the selected
Django backend; the container layer does not change transaction behavior.

MongoDB initializes a single-node `rs0` replica set and waits for a writable
primary before application migration. The initializer accepts an existing
configuration without replacing it. MongoDB 8.0.4 is a local compatibility
fixture for the kernel restriction observed with newer images, not a
recommended production patch level. Set `EXAMPLE_MONGO_IMAGE` to a supported,
patched image for your host. No kernel/security check is disabled.

### Tenant fixture

```console
docker compose -f examples/compose.yaml up --build --wait tenancy
docker compose -f examples/compose.yaml exec tenancy python manage.py create_demo_tenants
curl -H 'Host: alpha.example.test' http://127.0.0.1:8124/notes/
```

The entrypoint uses `migrate_schemas`. Create the demo tenants once, as the
fixture command deliberately refuses to replace existing tenants. It is not
part of restart-time migration.

### Search, native cache and Celery

The container profile enables both Elasticsearch paths, native Valkey cache
and a real broker. The host-based example retains its offline/eager defaults.
Start the worker explicitly alongside the application:

```console
docker compose -f examples/compose.yaml up --build --wait ecosystem-services celery-worker
docker compose -f examples/compose.yaml exec ecosystem-services python manage.py search_index --create
curl -H 'Content-Type: application/json' -d '{"title":"Example"}' http://127.0.0.1:8123/jobs/
curl -H 'Content-Type: application/json' -d '{"title":"Cached"}' http://127.0.0.1:8123/cache/
curl http://127.0.0.1:8123/cache/
```

Run `search_index --create` only for a new example index. It is intentionally
not run at every startup. See the [service example](ecosystem-services/README.md)
for native/Django read and write requests, explicit indexing, broker failure
semantics and live integration tests. The Celery worker starts after the API
has migrated the shared SQLite database. SQLite and a single-process worker
keep this demonstration small; they are not a high-concurrency queue design.

The default service tests deliberately exercise offline/eager behavior. Clear
the container's service configuration for that suite, then run live tests
separately with a new test-only index name:

```console
docker compose -f examples/compose.yaml exec ecosystem-services env -u EXAMPLE_CELERY_BROKER -u EXAMPLE_SEARCH_URL -u EXAMPLE_VALKEY_URL DJANGO_SETTINGS_MODULE=project.settings pytest -q
docker compose -f examples/compose.yaml exec ecosystem-services env -u EXAMPLE_CELERY_BROKER EXAMPLE_SEARCH_INDEX=aiodrf-example-test-docker pytest -q tests/test_nosql_live.py
```

The live test refuses to reuse an existing index and removes only the index it
created. Test jobs use eager execution against the test database; an external
worker must not consume IDs belonging to an isolated pytest database.

Valkey uses separate logical databases for cache and broker traffic and
`noeviction` so a full instance does not silently evict queued jobs. In a
deployment, use separate broker/cache instances with suitable persistence
and capacity policies. Elasticsearch is single-node, security disabled, with
a bounded heap. Do not expose these services externally.

S3, Sentry and hosted telemetry remain optional external integrations.
Containers do not receive host cloud credentials automatically and do not
provision cloud resources. Follow the example's credential/permission guide
and pass credentials through an explicit secret mechanism if enabling them.

### Django cache and platform profiles

Start only the cache service required by the chosen backend:

```console
docker compose -f examples/compose.yaml up --wait valkey
EXAMPLE_CACHE=redis docker compose -f examples/compose.yaml up --build --wait django-builtin
docker compose -f examples/compose.yaml up -d memcached
EXAMPLE_CACHE=pymemcache docker compose -f examples/compose.yaml up --build --wait django-builtin
```

The `database` cache profile creates its cache table during startup.
`locmem`, `dummy` and `file` need no external service. The optional
`pylibmc` driver is not installed in the shared image; it also needs native
libmemcached build libraries. Use the supplied `pymemcache` profile for a
self-contained Memcached example, or extend the image deliberately.

Platform settings can be selected without editing source:

```console
EXAMPLE_PLATFORM_SETTINGS=project.settings_whitenoise_async docker compose -f examples/compose.yaml up --build --wait ecosystem-platform
AIODRF_EXAMPLE_PROFILE=tuned-compiled docker compose -f examples/compose.yaml up --build --wait serializer-backends
```

Use the exact profile names in the relevant README. Optional upstream-warning
profiles and vendor credentials are not enabled by default.

## Rebuilds, state and shutdown

Images include a copy of the checkout; source is not bind-mounted. Rebuild after
editing Python code. Dependency ranges are resolved at build time, without a
committed lock file. Retain the image digest and `uv pip list` output when
reproducing a particular environment. To refresh dependency resolution rather
than reuse a cached layer, build the selected service with `--no-cache`.

Ordinary examples keep SQLite and uploads in the container's writable layer.
They survive a stop/start but not container replacement. The external database
examples and service worker share named volumes. Use a different Compose
project name (`-p`) when you need a separate set of databases; keep that name
on subsequent commands.

Stop the services you started, leaving their data intact:

```console
docker compose -f examples/compose.yaml stop ecosystem-services celery-worker valkey elasticsearch
```

`docker compose -f examples/compose.yaml --profile examples down` removes
this Compose project's containers and network, but retains named volumes.
Adding `--volumes` permanently removes their data; do that only when an
explicit reset is intended. Never use a broad Docker prune command to clean
up an example.

## Configuration sources

- [Dockerfile](Dockerfile): interpreter, installer, isolation and health check.
- [Compose configuration](compose.yaml): application ports and local services.
- [Container entrypoint](container_entrypoint.py): migrations and process handoff.
- [Docker Compose profiles](https://docs.docker.com/compose/how-tos/profiles/):
  explicit-service startup and dependency resolution.
- [uv in Docker](https://docs.astral.sh/uv/guides/integration/docker/):
  installer images and build reproducibility.
