# Opt-in native PostgreSQL ORM

Explicit native ModelSerializer/ModelViewSet, async_objects and the backend-owned connection lifecycle.

## Run with Docker

From the repository root:

```console
docker compose -f examples/compose.yaml up --build --wait native-postgres
docker compose -f examples/compose.yaml exec native-postgres python manage.py check
```

The API is available on `http://127.0.0.1:8115`. See
[container setup](../CONTAINERS.md) for port overrides, required services,
optional profiles, tests and data retention. The commands below run the same
application directly with uv.

## Run

From this directory (Python 3.12+ and uv):

```console
uv venv
uv pip install --python .venv/bin/python -r pyproject.toml --group test -e ../..
uv run --no-sync python manage.py check
uv run --no-sync python manage.py migrate
uv run --no-sync uvicorn project.asgi:application --host 127.0.0.1 --port 8115
```

Change `--host` and `--port` as needed. For another hostname also set
`EXAMPLE_ALLOWED_HOSTS=localhost,127.0.0.1,your-host`. The default is loopback.
This is an independent uv project using the local aiodrf checkout through
`[tool.uv.sources]`; it imports no other example.

Prerequisite: **PostgreSQL**, configured before migration or startup. See below.

## Requests

```console
# Supply a dedicated local database, never a production database.
PGHOST=127.0.0.1 PGPORT=5432 PGDATABASE=aiodrf_example PGUSER=example PGPASSWORD=local uv run --no-sync python manage.py migrate
curl -X POST http://127.0.0.1:8115/notes/ -H 'Content-Type: application/json' -d '{"title":"Native"}'
curl http://127.0.0.1:8115/notes/
```

## Tests

```console
uv run --no-sync pytest -q
```

Tests exercise the actual ASGI application with lifespan startup/shutdown.
They require the named service and use `test_aiodrf_example_native`; unavailable
services fail, not silently skip. Do not use that reserved test name for
application data.

## Compatibility and limits

Set PGHOST, PGPORT, PGDATABASE, PGUSER and PGPASSWORD for both migrate and server commands. Use a dedicated database and a role allowed to create its isolated test database. When its app is installed, django-async-backend adds its manager to Django's
`Model`; aiodrf itself patches nothing. Native writes and the queries of
synchronous signal handlers use separate connections, so they do not share a
transaction. Measure performance against the standard ORM on the same deployment.

The settings use a development secret key and a deliberately simple
permission policy: do not deploy this project as it is.
See the [feature guide](../../docs/guides/async-backend.md) and the [example catalogue](../README.md).
