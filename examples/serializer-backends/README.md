# DRF fields, compilers and tuned profiles

The same DRF model fields under normal, strict-msgspec, strict-pydantic and tuned profiles; per-serializer override, field-template caching, relation batching and explicit read/write serialization.

## Run with Docker

From the repository root:

```console
docker compose -f examples/compose.yaml up --build --wait serializer-backends
docker compose -f examples/compose.yaml exec serializer-backends python manage.py check
```

The API is available on `http://127.0.0.1:8106`. See
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
uv run --no-sync uvicorn project.asgi:application --host 127.0.0.1 --port 8106
```

Change `--host` and `--port` as needed. For another hostname also set
`EXAMPLE_ALLOWED_HOSTS=localhost,127.0.0.1,your-host`. The default is loopback.
This project depends on this checkout through `[tool.uv.sources]`; it does not
import another example. Its virtual environment and SQLite database are local.

## Requests

```console
curl -X POST http://127.0.0.1:8106/articles/ -H 'Content-Type: application/json' -d '{"title":"First","tags":[]}'
curl http://127.0.0.1:8106/articles/
curl http://127.0.0.1:8106/profile/
# Restart with a profile; profiles are never switched on a live app.
AIODRF_EXAMPLE_PROFILE=tuned uv run --no-sync uvicorn project.asgi:application --host 127.0.0.1 --port 8106
uv run --no-sync python manage.py aiodrf_inspect_serializers --serializer demo.views.Read --format json
```

## Tests

```console
uv run --no-sync pytest -q
```

The tests send requests through the project's ASGI application, with lifespan
startup/shutdown. Database tests use an isolated test database.

## Compatibility and limits

| Profile | Behavior |
| --- | --- |
| `normal` | DRF validation and output, default settings |
| `strict-msgspec` | Eligible output uses msgspec with strict DRF parity |
| `strict-pydantic` | Eligible output uses Pydantic with strict DRF parity |
| `tuned` | Fast compiler parity, field-template caching and relation batching |
| `tuned-clone` | Tuned plus opt-in `FIELD_COPY_MODE="clone"`; eligible exact scalar fields avoid constructor replay |
| `tuned-compiled` | Tuned plus recursive copy plans; nested/container constructor behavior and unsupported custom copies stay on DRF |

`/selected-articles/` enables cached compiled field copying only for that view.
`PerSerializer.Meta` demonstrates serializer-level selection on the
`explicit_backend` action. These options do not change process-wide settings;
the nearest serializer Meta takes precedence over view attributes.

Select a profile with `AIODRF_EXAMPLE_PROFILE` before starting the process.
The listed profiles have request/response equivalence tests for this model.
Cloning retains DRF deepcopy for custom, nested and relational fields. It
requires the static field-template cache and does not modify DRF classes.

Tuned is opt-in: fast parity is not universally DRF-equivalent (notably Decimal
handling). This model deliberately contains no Decimal field. Both the per-class
backend and the global backend have explicit endpoints. POST re-reads saved
relations, preserving database ordering and signal effects. Serialization
is CPU work, not asynchronous I/O.

The endpoints are public and the settings use a development secret key: do
not deploy this project as it is.
See the [feature guide](../../docs/guides/performance.md) and
[example catalogue](../README.md) for related examples.
