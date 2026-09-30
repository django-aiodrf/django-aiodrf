# NDJSON, JSON arrays and server-sent events

StreamingResponse, StreamingArrayResponse, EventStreamResponse, keepalive and deterministic generator cleanup; item OpenAPI annotations.

## Run with Docker

From the repository root:

```console
docker compose -f examples/compose.yaml up --build --wait streaming
docker compose -f examples/compose.yaml exec streaming python manage.py check
```

The API is available on `http://127.0.0.1:8102`. See
[container setup](../CONTAINERS.md) for port overrides, required services,
optional profiles, tests and data retention. The commands below run the same
application directly with uv.

## Run

From this directory (Python 3.12+ and uv):

```console
uv venv
uv pip install --python .venv/bin/python -r pyproject.toml --group test -e ../..
uv run --no-sync python manage.py check
uv run --no-sync uvicorn project.asgi:application --host 127.0.0.1 --port 8102
```

Change `--host` and `--port` as needed. For another hostname also set
`EXAMPLE_ALLOWED_HOSTS=localhost,127.0.0.1,your-host`. The default is loopback.
This project depends on this checkout through `[tool.uv.sources]`; it does not
import another example. Its virtual environment and SQLite database are local.

## Requests

```console
curl -N http://127.0.0.1:8102/ndjson/
curl -N http://127.0.0.1:8102/events/
curl http://127.0.0.1:8102/array/
```

## Tests

```console
uv run --no-sync pytest -q
```

The tests send requests through the project's ASGI application, with lifespan
startup/shutdown. Database tests use an isolated test database.

## Compatibility and limits

These are finite three-item streams. A real source belongs in the generator's try/finally. Proxy buffering and GZip can delay events; see the stream proxy guide before deployment. An ASGI client test does not simulate a TCP disconnect.

The endpoints are public and the settings use a development secret key: do
not deploy this project as it is.
See the [feature guide](../../docs/guides/streaming-schema.md) and
[example catalogue](../README.md) for related examples.
