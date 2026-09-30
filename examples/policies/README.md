# Authentication, permissions, caching and QUERY

Token/Basic/session authentication, CSRF middleware, async authentication challenges, composed permissions, fixed-window throttling, public cache_page, ETags and explicit QUERY handling.

## Run with Docker

From the repository root:

```console
docker compose -f examples/compose.yaml up --build --wait policies
docker compose -f examples/compose.yaml exec policies python manage.py check
```

The API is available on `http://127.0.0.1:8108`. See
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
uv run --no-sync uvicorn project.asgi:application --host 127.0.0.1 --port 8108
```

Change `--host` and `--port` as needed. For another hostname also set
`EXAMPLE_ALLOWED_HOSTS=localhost,127.0.0.1,your-host`. The default is loopback.
This is an independent uv project using the local aiodrf checkout through
`[tool.uv.sources]`; it imports no other example.

## Requests

```console
curl http://127.0.0.1:8108/public/
curl -i http://127.0.0.1:8108/conditional/ -H 'If-None-Match: "demo-v1"'
curl -X QUERY http://127.0.0.1:8108/search/ -H 'Content-Type: application/json' -d '{"term":"Ada"}'
# Create an account and token locally, then set TOKEN yourself.
uv run --no-sync python manage.py createsuperuser
uv run --no-sync python manage.py drf_create_token YOUR_USERNAME
curl http://127.0.0.1:8108/private/ -H "Authorization: Token $TOKEN"
```

## Tests

Open `/docs/` for Swagger UI and `/schema/` for the drf-spectacular schema.
`POST /search/` is the documented alternative to `QUERY /search/`, with the same
input/output serializer contract. OpenAPI 3.0/3.1 cannot express QUERY, so the
preprocessing hook excludes it rather than producing an invalid operation.
Generated clients use POST; direct HTTP clients can use QUERY. See the
[QUERY guide](../../docs/guides/http-query.md) for CSRF, caching and Django compatibility.

```console
uv run --no-sync pytest -q
```

Tests exercise the actual ASGI application with lifespan startup/shutdown.
Database tests use an isolated test database.

## Compatibility and limits

The cached endpoint is public; never apply this cache key policy to per-user data without correct Vary/private handling. LocMemCache limits are per process. QUERY is not treated as a safe method by every proxy, schema generator or third-party permission. The example explicitly opts it in on a public read-only handler. Conditional checks run after permissions, but an ETag is not a database lock.

The settings use a development secret key and a deliberately simple
permission policy: do not deploy this project as it is.
See the [feature guide](../../docs/guides/ecosystem.md) and the [example catalogue](../README.md).
