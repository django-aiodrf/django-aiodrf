# Batched and bounded-concurrent enrichment

PrefetchListSerializer for one batch per list; ConcurrentListSerializer for independent item I/O with separate item contexts and stable ordering.

## Run with Docker

From the repository root:

```console
docker compose -f examples/compose.yaml up --build --wait list-enrichment
docker compose -f examples/compose.yaml exec list-enrichment python manage.py check
```

The API is available on `http://127.0.0.1:8104`. See
[container setup](../CONTAINERS.md) for port overrides, required services,
optional profiles, tests and data retention. The commands below run the same
application directly with uv.

## Run

From this directory (Python 3.12+ and uv):

```console
uv venv
uv pip install --python .venv/bin/python -r pyproject.toml --group test -e ../..
uv run --no-sync python manage.py check
uv run --no-sync uvicorn project.asgi:application --host 127.0.0.1 --port 8104
```

Change `--host` and `--port` as needed. For another hostname also set
`EXAMPLE_ALLOWED_HOSTS=localhost,127.0.0.1,your-host`. The default is loopback.
This project depends on this checkout through `[tool.uv.sources]`; it does not
import another example. Its virtual environment and SQLite database are local.

## Requests

```console
curl http://127.0.0.1:8104/batch/
curl http://127.0.0.1:8104/concurrent/
```

## Tests

```console
uv run --no-sync pytest -q
```

The tests send requests through the project's ASGI application, with lifespan
startup/shutdown. Database tests use an isolated test database.

## Compatibility and limits

The service delay is simulated. Prefer the batch API when the real service
provides one. Concurrency is limited to two items at a time; do not use it to run
ORM transactions in parallel.

The endpoints are public and the settings use a development secret key: do
not deploy this project as it is.
See the [feature guide](../../docs/guides/concurrent-serialization.md) and
[example catalogue](../README.md) for related examples.
