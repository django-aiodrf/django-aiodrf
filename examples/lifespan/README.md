# Typed resources and async commands

Config-path async context manager, typed request state, deterministic HTTP client shutdown and an AsyncCommand.

## Run with Docker

From the repository root:

```console
docker compose -f examples/compose.yaml up --build --wait lifespan
docker compose -f examples/compose.yaml exec lifespan python manage.py check
```

The API is available on `http://127.0.0.1:8103`. See
[container setup](../CONTAINERS.md) for port overrides, required services,
optional profiles, tests and data retention. The commands below run the same
application directly with uv.

## Run

From this directory (Python 3.12+ and uv):

```console
uv venv
uv pip install --python .venv/bin/python -r pyproject.toml --group test -e ../..
uv run --no-sync python manage.py check
uv run --no-sync uvicorn project.asgi:application --host 127.0.0.1 --port 8103
```

Change `--host` and `--port` as needed. For another hostname also set
`EXAMPLE_ALLOWED_HOSTS=localhost,127.0.0.1,your-host`. The default is loopback.
This project depends on this checkout through `[tool.uv.sources]`; it does not
import another example. Its virtual environment and SQLite database are local.

## Requests

```console
curl http://127.0.0.1:8103/resource/
uv run --no-sync python manage.py probe
```

## Tests

```console
uv run --no-sync pytest -q
```

The tests send requests through the project's ASGI application, with lifespan
startup/shutdown. Database tests use an isolated test database.

## Compatibility and limits

The HTTP service is simulated with HTTPX's `MockTransport`; in your application,
configure a real URL and timeouts. Django's checks and the schema command do not
start the lifespan resources.

The endpoints are public and the settings use a development secret key: do
not deploy this project as it is.
See the [feature guide](../../docs/guides/lifespan.md) and
[example catalogue](../README.md) for related examples.
