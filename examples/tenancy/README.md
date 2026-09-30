# PostgreSQL tenant schemas

An independent django-tenants project: the vendor engine, database router and
middleware select a schema before aiodrf dispatches the view. No schema selection
patch or global tenant variable is added.

## Run with Docker

From the repository root:

```console
docker compose -f examples/compose.yaml up --build --wait tenancy
docker compose -f examples/compose.yaml exec tenancy python manage.py check
```

The API is available on `http://127.0.0.1:8124`. See
[container setup](../CONTAINERS.md) for port overrides, required services,
optional profiles, tests and data retention. The commands below run the same
application directly with uv.

## Run

Use a dedicated PostgreSQL database/role with schema creation privileges. Supply
PGHOST, PGPORT, PGDATABASE, PGUSER and PGPASSWORD through your local environment.
The default database name is aiodrf_example_tenants. Never use production data.

```console
uv venv
uv pip install --python .venv/bin/python -r pyproject.toml --group test -e ../..
uv run --no-sync python manage.py migrate_schemas --shared
uv run --no-sync python manage.py create_demo_tenants
uv run --no-sync uvicorn project.asgi:application --host 127.0.0.1 --port 8124
uv run --no-sync pytest -q
curl -H 'Host: alpha.example.test' http://127.0.0.1:8124/notes/
curl -H 'Host: beta.example.test' http://127.0.0.1:8124/texts/
```

The command creates only alpha and beta and refuses to overwrite existing tenant
metadata. Configure your hosts file or use Host headers for local requests.
EXAMPLE_ALLOWED_HOSTS can override the allowed hostnames. The API permits local
anonymous writes to keep the schema boundary visible; real deployments also
need tenant membership and object authorization.

## Isolation contract

Django's ASGI handler gives each request its own thread-sensitive context.
aiodrf's synchronous hooks and Django's async ORM wrappers use that context.
The test sends concurrent requests through the complete ASGI application, checks
both schemas and verifies that a row created in one is absent from the other.
Calling views directly inside one shared async test-client context is not an
adequate tenant isolation test.

Tests create only test_aiodrf_example_tenants and the schemas inside it. The
fixture removes the schemas it created; it never drops a configured application
database. Native django-async-backend connections are a separate integration and
are not claimed to share django-tenants' connection-state contract.
