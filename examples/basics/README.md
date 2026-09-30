# Async hooks and DRF coexistence

APIView, decorators, async field validation, serializer factories, exception/finalize hooks and an unchanged synchronous DRF view.

## Run with Docker

From the repository root:

```console
docker compose -f examples/compose.yaml up --build --wait basics
docker compose -f examples/compose.yaml exec basics python manage.py check
```

The API is available on `http://127.0.0.1:8101`. See
[container setup](../CONTAINERS.md) for port overrides, required services,
optional profiles, tests and data retention. The commands below run the same
application directly with uv.

## Run

From this directory (Python 3.12+ and uv):

```console
uv venv
uv pip install --python .venv/bin/python -r pyproject.toml --group test -e ../..
uv run --no-sync python manage.py check
uv run --no-sync uvicorn project.asgi:application --host 127.0.0.1 --port 8101
```

Change `--host` and `--port` as needed. For another hostname also set
`EXAMPLE_ALLOWED_HOSTS=localhost,127.0.0.1,your-host`. The default is loopback.
This project depends on this checkout through `[tool.uv.sources]`; it does not
import another example. Its virtual environment and SQLite database are local.

## Requests

### Granian alternative

The same application can be served by the optional Granian dependency:

```console
uv pip install --python .venv/bin/python -e '../..[granian]'
uv run --no-sync granian --interface asgi --host 127.0.0.1 --port 8101 --workers 1 project.asgi:application
```

Use this instead of the Uvicorn command, not on the same port at the same time.
No application code or middleware replacement is required. See
[server selection](../../docs/guides/web-servers.md) for lifespan and worker limits.

### HTTP requests

```console
curl -X POST http://127.0.0.1:8101/echo/ -H 'Content-Type: application/json' -d '{"name":"Ada"}'
curl http://127.0.0.1:8101/sync/
```

## Tests

```console
uv run --no-sync pytest -q
```

The tests send requests through the project's ASGI application, with lifespan
startup/shutdown. Database tests use an isolated test database.

## Compatibility and limits

Synchronous callbacks run in a worker thread. An async hook must use non-blocking
I/O: declaring a function `async def` does not make a blocking client
asynchronous.

The endpoints are public and the settings use a development secret key: do
not deploy this project as it is.
See the [feature guide](../../docs/guides/extension-hooks.md) and
[example catalogue](../README.md) for related examples.
