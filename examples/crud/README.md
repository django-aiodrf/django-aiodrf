# CRUD, filters and pagination

ModelViewSet, normal DRF router/actions, django-filter, search/ordering, page/limit-offset/cursor pagination and PATCH.

## Run with Docker

From the repository root:

```console
docker compose -f examples/compose.yaml up --build --wait crud
docker compose -f examples/compose.yaml exec crud python manage.py check
```

The API is available on `http://127.0.0.1:8107`. See
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
uv run --no-sync uvicorn project.asgi:application --host 127.0.0.1 --port 8107
```

Change `--host` and `--port` as needed. For another hostname also set
`EXAMPLE_ALLOWED_HOSTS=localhost,127.0.0.1,your-host`. The default is loopback.
This project depends on this checkout through `[tool.uv.sources]`; it does not
import another example. Its virtual environment and SQLite database are local.

## Requests

```console
curl -X POST http://127.0.0.1:8107/articles/ -H 'Content-Type: application/json' -d '{"title":"First","tags":[]}'
curl 'http://127.0.0.1:8107/articles/?search=First'
curl 'http://127.0.0.1:8107/offset/?limit=1'
curl http://127.0.0.1:8107/cursor/
```

## Tests

```console
uv run --no-sync pytest -q
```

The tests send requests through the project's ASGI application, with lifespan
startup/shutdown. Database tests use an isolated test database.

## Compatibility and limits

On Django 6.1+, restart with `EXAMPLE_FETCH_MODE=raise` to expose accidental
lazy relation reads, or `EXAMPLE_FETCH_MODE=peers` to opt into peer fetching.
Neither mode replaces explicit queryset design. Leave it unset on Django 5.2.

Default synchronous ORM work stays in Django's thread-sensitive worker. Page counts still execute SQL. Use the serializer-backends example for opt-in compilation and relation batching; neither is required for CRUD.

The endpoints are public and the settings use a development secret key: do
not deploy this project as it is.
See the [feature guide](../../docs/guides/drf-integration.md) and
[example catalogue](../README.md) for related examples.
