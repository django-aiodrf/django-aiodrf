# Isolated ADRF import compatibility and codemod

Opt-in ADRF import shim, legacy async property and a non-mutating codemod preview.

## Run with Docker

From the repository root:

```console
docker compose -f examples/compose.yaml up --build --wait migration
docker compose -f examples/compose.yaml exec migration python manage.py check
```

The API is available on `http://127.0.0.1:8113`. See
[container setup](../CONTAINERS.md) for port overrides, required services,
optional profiles, tests and data retention. The commands below run the same
application directly with uv.

## Run

From this directory (Python 3.12+ and uv):

```console
uv venv
uv pip install --python .venv/bin/python -r pyproject.toml --group test -e ../..
uv run --no-sync python manage.py check
uv run --no-sync uvicorn project.asgi:application --host 127.0.0.1 --port 8113
```

Change `--host` and `--port` as needed. For another hostname also set
`EXAMPLE_ALLOWED_HOSTS=localhost,127.0.0.1,your-host`. The default is loopback.
This is an independent uv project using the local aiodrf checkout through
`[tool.uv.sources]`; it imports no other example.

## Requests

```console
curl http://127.0.0.1:8113/legacy/
uv run --no-sync python -m aiodrf.codemod --diff demo/views.py
```

## Tests

```console
uv run --no-sync pytest -q
```

Tests exercise the actual ASGI application with lifespan startup/shutdown.
Database tests use an isolated test database.

## Compatibility and limits

This explicit migration environment must not contain the real adrf distribution. The shim installs sys.modules aliases and emits a deprecation warning. The --diff command does not change files. Migrate to normal aiodrf imports and disable ADRF_COMPAT after validating application behavior.

The settings use a development secret key and a deliberately simple
permission policy: do not deploy this project as it is.
See the [feature guide](../../docs/guides/migration-from-adrf.md) and the [example catalogue](../README.md).
