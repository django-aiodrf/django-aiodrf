# SimpleJWT, Knox and auth-kit

Vendor authentication classes on aiodrf views; opt-in missing-credential checks for SimpleJWT, Knox and drf-auth-kit.

## Run with Docker

From the repository root:

```console
docker compose -f examples/compose.yaml up --build --wait vendor-authentication
docker compose -f examples/compose.yaml exec vendor-authentication python manage.py check
```

The API is available on `http://127.0.0.1:8117`. See
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
uv run --no-sync uvicorn project.asgi:application --host 127.0.0.1 --port 8117
```

Change `--host` and `--port` as needed. For another hostname also set
`EXAMPLE_ALLOWED_HOSTS=localhost,127.0.0.1,your-host`. The default is loopback.
This is an independent uv project using the local aiodrf checkout through
`[tool.uv.sources]`; it imports no other example.

## Requests

```console
uv run --no-sync python manage.py createsuperuser
curl -X POST http://127.0.0.1:8117/token/ -H 'Content-Type: application/json' -d '{"username":"YOUR_USERNAME","password":"YOUR_PASSWORD"}'
# Copy access to JWT; no token is committed in this example.
curl http://127.0.0.1:8117/jwt/ -H "Authorization: Bearer $JWT"
```

## Tests

```console
uv run --no-sync pytest -q
```

Tests exercise the actual ASGI application with lifespan startup/shutdown.
Database tests use an isolated test database.

## Compatibility and limits

Vendor libraries retain token validation, expiry, revocation and cookie semantics; aiodrf only skips a credential check when it can prove no credential is present. The sample exposes JWT login but no custom Knox/cookie login flow: its tests create those credentials through the vendor APIs. Cookie authentication must use an application CSRF/origin policy. Do not deploy these public development settings.

The settings use a development secret key and a deliberately simple
permission policy: do not deploy this project as it is.
See the [feature guide](../../docs/guides/ecosystem.md) and the [example catalogue](../README.md).
