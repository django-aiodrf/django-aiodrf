# Typed inputs, partial updates and OpenAPI

Explicit msgspec/Pydantic serializers, input/output schemas, partial validation, msgspec JSON codec, query validation and Swagger.

Native schemas validate directly with Pydantic or msgspec; they are not converted
to writable DRF fields. The endpoints demonstrate distinct contracts:

| Endpoint | Contract |
| --- | --- |
| `/msgspec/`, `/pydantic/` | Separate input/output schemas and field-level PATCH |
| `/pydantic-context/` | Pydantic validator and field serializer using DRF context |
| `/msgspec-custom/` | Application type with decode, encode and JSON Schema hooks |
| `/root-list/` | Pydantic `RootModel[list[int]]`, preserving an array response |
| `/query/` | DRF query parameter validation |
| `/schema/`, `/docs/` | OpenAPI and Swagger from the same schema definitions |

## Run with Docker

From the repository root:

```console
docker compose -f examples/compose.yaml up --build --wait typed-schemas
docker compose -f examples/compose.yaml exec typed-schemas python manage.py check
```

The API is available on `http://127.0.0.1:8105`. See
[container setup](../CONTAINERS.md) for port overrides, required services,
optional profiles, tests and data retention. The commands below run the same
application directly with uv.

## Run

From this directory (Python 3.12+ and uv):

```console
uv venv
uv pip install --python .venv/bin/python -r pyproject.toml --group test -e ../..
uv run --no-sync python manage.py check
uv run --no-sync uvicorn project.asgi:application --host 127.0.0.1 --port 8105
```

Change `--host` and `--port` as needed. For another hostname also set
`EXAMPLE_ALLOWED_HOSTS=localhost,127.0.0.1,your-host`. The default is loopback.
This project depends on this checkout through `[tool.uv.sources]`; it does not
import another example. Its virtual environment and SQLite database are local.

## Requests

```console
curl -X POST http://127.0.0.1:8105/msgspec/ -H 'Content-Type: application/json' -d '{"name":"Ada","count":2}'
curl -X PATCH http://127.0.0.1:8105/pydantic/ -H 'Content-Type: application/json' -d '{"count":3}'
curl http://127.0.0.1:8105/query/?limit=3
curl -X POST http://127.0.0.1:8105/pydantic-context/ -H 'Content-Type: application/json' -d '{"name":" Ada "}'
curl -X POST http://127.0.0.1:8105/msgspec-custom/ -H 'Content-Type: application/json' -d '{"reference":"book:42"}'
curl -X POST http://127.0.0.1:8105/root-list/ -H 'Content-Type: application/json' -d '[1,2,3]'
# Open http://127.0.0.1:8105/docs/
```

## Tests

```console
uv run --no-sync pytest -q
```

The tests send requests through the project's ASGI application, with lifespan
startup/shutdown. Database tests use an isolated test database.

## Compatibility and limits

Validation errors come from msgspec or Pydantic, so their wording can differ
from DRF's. Compiling existing DRF field declarations is demonstrated separately in serializer-backends. Swagger uses its configured CDN; schema generation itself needs no network.

The example retains default worker execution for custom synchronous callbacks.
The context endpoint supplies application values through `get_serializer_context`;
it does not keep request state on a shared model or backend. Vendor callbacks
must be synchronous. Root values do not acquire field-level PATCH semantics.
The custom msgspec type's JSON Schema hook documents, but does not validate, its
wire representation; the decode hook performs that validation.

The endpoints are public and the settings use a development secret key: do
not deploy this project as it is.
See the [feature guide](../../docs/guides/msgspec-pydantic.md) and
[example catalogue](../README.md) for related examples.
