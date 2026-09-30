# Django 5 task enqueue

django-tasks backport using its own imports/settings, sync and async jobs and a dummy queue.

## Run with Docker

From the repository root:

```console
docker compose -f examples/compose.yaml up --build --wait tasks-django5
docker compose -f examples/compose.yaml exec tasks-django5 python manage.py check
```

The API is available on `http://127.0.0.1:8110`. See
[container setup](../CONTAINERS.md) for port overrides, required services,
optional profiles, tests and data retention. The commands below run the same
application directly with uv.

## Run

From this directory (Python 3.12+ and uv):

```console
uv venv
uv pip install --python .venv/bin/python -r pyproject.toml --group test -e ../..
uv run --no-sync python manage.py check
uv run --no-sync uvicorn project.asgi:application --host 127.0.0.1 --port 8110
```

Change `--host` and `--port` as needed. For another hostname also set
`EXAMPLE_ALLOWED_HOSTS=localhost,127.0.0.1,your-host`. The default is loopback.
This project depends on this checkout through `[tool.uv.sources]`; it does not
import another example. Its virtual environment and SQLite database are local.

## Requests

```console
curl -X POST http://127.0.0.1:8110/enqueue/ -H 'Content-Type: application/json' -d '{"value":4}'
curl -X POST http://127.0.0.1:8110/queue/ -H 'Content-Type: application/json' -d '{"value":4}'
```

## Tests

```console
uv run --no-sync pytest -q
```

The tests send requests through the project's ASGI application, with lifespan
startup/shutdown. Database tests use an isolated test database.

## Compatibility and limits

ImmediateBackend runs before the HTTP response; DummyBackend records without execution. Neither is a durable production queue. Choose and operate a production backend/worker separately. Enqueue after database commit when the job reads rows created by the request. No task wrapper, import shim or worker is installed by aiodrf.

The endpoints are public and the settings use a development secret key: do
not deploy this project as it is.
See the [feature guide](../../docs/guides/tasks.md) and
[example catalogue](../README.md) for related examples.
