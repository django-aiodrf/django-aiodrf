# Application-owned OpenTelemetry

Opt-in request phase spans, an application-owned tracer provider, console exporter and lifespan shutdown.

## Run with Docker

From the repository root:

```console
docker compose -f examples/compose.yaml up --build --wait telemetry
docker compose -f examples/compose.yaml exec telemetry python manage.py check
```

The API is available on `http://127.0.0.1:8112`. See
[container setup](../CONTAINERS.md) for port overrides, required services,
optional profiles, tests and data retention. The commands below run the same
application directly with uv.

## Run

From this directory (Python 3.12+ and uv):

```console
uv venv
uv pip install --python .venv/bin/python -r pyproject.toml --group test -e ../..
uv run --no-sync python manage.py check
uv run --no-sync uvicorn project.asgi:application --host 127.0.0.1 --port 8112
```

Change `--host` and `--port` as needed. For another hostname also set
`EXAMPLE_ALLOWED_HOSTS=localhost,127.0.0.1,your-host`. The default is loopback.
This is an independent uv project using the local aiodrf checkout through
`[tool.uv.sources]`; it imports no other example.

## Requests

```console
curl http://127.0.0.1:8112/traced/
# Read the emitted spans on the server's stdout.
```

## Tests

```console
uv run --no-sync pytest -q
```

Tests exercise the actual ASGI application with lifespan startup/shutdown.
Database tests use an isolated test database.

## Compatibility and limits

The example configures a console exporter, not a remote collector. Set sampling/exporters in the deployment, not in aiodrf. Unexpected exception events may contain application exception text; apply the application's redaction policy. A provider is process-owned, so restart the process when changing it.

The settings use a development secret key and a deliberately simple
permission policy: do not deploy this project as it is.
See the [feature guide](../../docs/guides/ecosystem.md) and the [example catalogue](../README.md).
