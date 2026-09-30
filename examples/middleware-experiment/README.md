# Explicit middleware scheduling experiment

Standard Django middleware by default; opt-in grouped synchronous subclasses in a separate process profile.

## Run with Docker

From the repository root:

```console
docker compose -f examples/compose.yaml up --build --wait middleware-experiment
docker compose -f examples/compose.yaml exec middleware-experiment python manage.py check
```

The API is available on `http://127.0.0.1:8114`. See
[container setup](../CONTAINERS.md) for port overrides, required services,
optional profiles, tests and data retention. The commands below run the same
application directly with uv.

## Run

From this directory (Python 3.12+ and uv):

```console
uv venv
uv pip install --python .venv/bin/python -r pyproject.toml --group test -e ../..
uv run --no-sync python manage.py check
uv run --no-sync uvicorn project.asgi:application --host 127.0.0.1 --port 8114
```

Change `--host` and `--port` as needed. For another hostname also set
`EXAMPLE_ALLOWED_HOSTS=localhost,127.0.0.1,your-host`. The default is loopback.
This is an independent uv project using the local aiodrf checkout through
`[tool.uv.sources]`; it imports no other example.

## Requests

```console
curl -i http://127.0.0.1:8114/headers/
# Stop the server before switching profiles.
AIODRF_EXAMPLE_PROFILE=grouped uv run --no-sync uvicorn project.asgi:application --host 127.0.0.1 --port 8114
```

## Tests

```console
uv run --no-sync pytest -q
```

Tests exercise the actual ASGI application with lifespan startup/shutdown.
Database tests use an isolated test database.

## Compatibility and limits

Grouped mode retains a worker while the view awaits. It is neither threadless middleware nor a recommended deployment default. Compare concurrency, cancellation and streaming in your own stack; no Django class is patched by these explicit subclasses.

The settings use a development secret key and a deliberately simple
permission policy: do not deploy this project as it is.
See the [feature guide](../../docs/guides/unsafe-middleware.md) and the [example catalogue](../README.md).
