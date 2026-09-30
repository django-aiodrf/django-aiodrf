# Bookshop: an example project on aiodrf

A Django project that combines the integrations below. Its tests call the full
ASGI application, with the lifespan enabled, through an in-process HTTP
transport.

## Run with Docker

From the repository root:

```console
docker compose -f examples/compose.yaml up --build --wait bookshop
docker compose -f examples/compose.yaml exec bookshop python manage.py check
```

The API is available on `http://127.0.0.1:8118`. See
[container setup](../CONTAINERS.md) for port overrides, required services,
optional profiles, tests and data retention. The commands below run the same
application directly with uv.

## Features

| Feature | Implementation | Guide |
| --- | --- | --- |
| A `ModelViewSet` with token authentication, django-filter, pagination and `Meta.auto_prefetch` | `catalog/views.py`, `catalog/serializers.py` | [migration from DRF](../../docs/guides/migration-from-drf.md) |
| One request to the stock service per page, not per book (`PrefetchListSerializer.aprefetch`) | `catalog/serializers.py` | [prefetch](../../docs/guides/prefetch.md) |
| The stock service's HTTP client, opened and closed with the server | `bookshop/lifecycle.py`, `AIODRF["LIFESPAN"]` | [lifespan](../../docs/guides/lifespan.md) |
| `304 Not Modified` and `412 Precondition Failed` from `aget_etag` | `BookViewSet.aget_etag` | [extension hooks](../../docs/guides/extension-hooks.md#conditional-requests) |
| A validated query string (`query_serializer_class`), documented as OpenAPI parameters | `BookSearch` | [implementation section 11](../../docs/guides/implementation.md#11-conditional-requests-query-parameters-and-query) |
| Newline-delimited JSON export from `QuerySet.aiterator()` | `BookViewSet.export` | [streaming](../../docs/guides/streaming-schema.md) |
| Throttles that count atomically in the cache | `REST_FRAMEWORK["DEFAULT_THROTTLE_CLASSES"]` | [implementation section 11](../../docs/guides/implementation.md#11-conditional-requests-query-parameters-and-query) |

## Run locally

```console
cd examples/bookshop
uv venv
uv pip install --python .venv/bin/python -r pyproject.toml --group test -e ../..
uv run --no-sync python manage.py migrate
uv run --no-sync uvicorn bookshop.asgi:application --host 127.0.0.1 --port 8118
curl http://127.0.0.1:8118/books/
uv run --no-sync pytest -q
```

The default inventory service is explicitly simulated with `httpx.MockTransport`;
stock values are SKU lengths, not real inventory. Set `STOCK_SERVICE_URL` to
use a remote service. Change `--host`/`--port` as needed and add a non-local
hostname through `EXAMPLE_ALLOWED_HOSTS`. Do not deploy the development secret.

The tests replace the stock service with their own `httpx.MockTransport`. They pass
their own lifespan factory to `get_asgi_application()`; the application code
does not change.

The test database is a file, not SQLite's in-memory default. Each ASGI
request has its own thread and database connection, and Django closes
connections only to a file database.
