# Multipart uploads and storage ownership

Multipart request parsing, validation, size limits and explicit storage I/O in a thread-sensitive worker.

## Run with Docker

From the repository root:

```console
docker compose -f examples/compose.yaml up --build --wait uploads
docker compose -f examples/compose.yaml exec uploads python manage.py check
```

The API is available on `http://127.0.0.1:8109`. See
[container setup](../CONTAINERS.md) for port overrides, required services,
optional profiles, tests and data retention. The commands below run the same
application directly with uv.

## Run

From this directory (Python 3.12+ and uv):

```console
uv venv
uv pip install --python .venv/bin/python -r pyproject.toml --group test -e ../..
uv run --no-sync python manage.py check
uv run --no-sync uvicorn project.asgi:application --host 127.0.0.1 --port 8109
```

Change `--host` and `--port` as needed. For another hostname also set
`EXAMPLE_ALLOWED_HOSTS=localhost,127.0.0.1,your-host`. The default is loopback.
This is an independent uv project using the local aiodrf checkout through
`[tool.uv.sources]`; it imports no other example.

## Requests

```console
curl -X POST http://127.0.0.1:8109/upload/ -F 'file=@README.md'
```

## Tests

```console
uv run --no-sync pytest -q
```

Tests exercise the actual ASGI application with lifespan startup/shutdown.
Database tests use an isolated test database.

## Compatibility and limits

The filesystem storage is local and non-public. The response exposes only the
generated storage name, never a filesystem path. For S3, configure Django
STORAGES with django-storages and credentials managed by your deployment.
The application owns upload validation, quotas, retention and object access.

The settings use a development secret key and a deliberately simple
permission policy: do not deploy this project as it is.
See the [feature guide](../../docs/guides/ecosystem.md) and the [example catalogue](../README.md).
