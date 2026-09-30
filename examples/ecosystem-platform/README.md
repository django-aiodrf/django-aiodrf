# Middleware, diagnostics and application services

This project groups operational integrations without enabling them all in one
middleware stack. Select a Django settings module at process startup. The
default project provides HTTP CRUD, health checks, ServeStatic assets, a typed
management command and Channels/DCRF WebSocket routing.

## Run with Docker

From the repository root:

```console
docker compose -f examples/compose.yaml up --build --wait ecosystem-platform
docker compose -f examples/compose.yaml exec ecosystem-platform python manage.py check
```

The API is available on `http://127.0.0.1:8122`. See
[container setup](../CONTAINERS.md) for port overrides, required services,
optional profiles, tests and data retention. The commands below run the same
application directly with uv.

## Run

```console
uv venv
uv pip install --python .venv/bin/python -r pyproject.toml --group test -e ../..
uv run --no-sync python manage.py migrate
uv run --no-sync uvicorn project.asgi:application --host 127.0.0.1 --port 8122
uv run --no-sync pytest -q
uv run --no-sync python manage.py count_records --prefix Catalogue
```

For another profile, set the same module for migration, server and tests:

```console
DJANGO_SETTINGS_MODULE=project.settings_protection uv run --no-sync python manage.py migrate
DJANGO_SETTINGS_MODULE=project.settings_protection uv run --no-sync uvicorn project.asgi:application --host 127.0.0.1 --port 8122
DJANGO_SETTINGS_MODULE=project.settings_protection uv run --no-sync pytest -q
```

Use `EXAMPLE_ALLOWED_HOSTS` for another host. Profiles in this table are checked
separately by `python examples/check.py ecosystem-platform` from the repository
root. No process-wide runtime settings switch or framework patch is installed.

## Profiles

| Settings module | Packages | Demonstration |
| --- | --- | --- |
| `project.settings` | django-health-check, ServeStatic, Channels, djangochannelsrestframework, django-typer | `/health/?format=json`, `/static/demo/status.txt`, `/ws/records/`, `count_records`. |
| `project.settings_whitenoise` | WhiteNoise | The same static asset through Django's WSGI test client. |
| `project.settings_whitenoise_async` | WhiteNoise + aiodrf adapter | Dual-mode middleware dispatch; file bodies still use WhiteNoise's synchronous iterator and buffer under ASGI. |
| `project.settings_logging` | django-structlog, django-log-request-id, drf-api-logger | `/ping/` binds the request ID; the API logger emits its vendor signal with sensitive keys excluded. Database logging is disabled. |
| `project.settings_protection` | django-axes, django-idempotency-key | `/identity/` uses BasicAuthentication lockout. Writes to `/records/` require an idempotency key; replay retains the vendor's default 409 response. |
| `project.settings_debug` | django-debug-toolbar | Local HTML diagnostics at `/diagnostics/`; internal-IP checks remain enabled. |
| `project.settings_silk` | django-silk | Request/SQL records, with authenticated/authorized profiler UI at `/silk/`. |
| `project.settings_prometheus` | django-prometheus | `/metrics` beside ordinary API routes. |
| `project.settings_cache` | django-cachalot, django-zeal | Query invalidation and an optional N+1 assertion context in application tests. |
| `project.settings_wireup` | Wireup | Constructor injection into `/injected/`; the vendor owns URL callback wrapping. |

Additional profiles require explicit services or credentials and are not started
by the default catalogue runner:

| Settings module | Configuration | Verification |
| --- | --- | --- |
| `project.settings_cacheops` | `EXAMPLE_CACHEOPS_URL`, default dedicated Redis DB 14 on port 6380. | Run the same pytest command with this settings module after starting your Redis instance. |
| `project.settings_apitally` | `EXAMPLE_APITALLY_CLIENT_ID` and optional `EXAMPLE_APITALLY_ENV`. Requests may be exported to the configured service. | The repository's offline `tests/ecosystem/test_apitally.py` verifies collection with transport disabled. Real credentials/export are an operator check. |

## Requests and WebSockets

```console
curl http://127.0.0.1:8122/health/?format=json
curl -H 'Idempotency-Key: local-create-1' -H 'Content-Type: application/json' -d '{"title":"public"}' http://127.0.0.1:8122/records/
curl -H 'X-Request-ID: local-1' http://127.0.0.1:8122/ping/
```

The WebSocket consumer accepts connections only while a record called `public`
exists. Send `{"action":"list","request_id":1}` after connecting. This is an
executable async permission example, not a user authorization policy. The origin
validator and normal Channels authentication stack remain in place.

## Operational boundaries

The default profile uses ServeStatic's ASGI-capable middleware. Its filesystem
operations still require worker threads; an async interface does not make disk
access nonblocking. Tests cover GET, HEAD, conditional requests and delegation
to the API. See the [static files guide](../../docs/guides/static-files.md).

Synchronous middleware remains synchronous; Django supplies its adapters.
WhiteNoise is retained in `project.settings_whitenoise` as a WSGI compatibility
profile. Its synchronous file iterator can produce Django's iterator-adaptation
warning under ASGI. Do not suppress that warning or replace Django response
methods. For the Uvicorn + Nginx deployment, serve collected static assets with
Nginx/CDN and measure the full middleware stack, not only a bare view.

Silk, cachalot and cacheops instrument the ORM themselves. They are opt-in
profiles; aiodrf adds no patch to them or to Django. Profilers can retain SQL,
headers and response data. Keep them local, restrict access and define retention.
Never infer production authorization or replay isolation from the local cache
defaults. Request log listeners must not perform blocking I/O on the event loop.

This environment uses Python 3.12 and Django 6.0 because django-prometheus
currently declares Django below 6.1. django-typer 4.1 still passes a deprecated
completion argument to Typer. Its command test explicitly asserts that vendor
warning rather than globally ignoring deprecations or downgrading the dependency.
DCRF and Apitally also have Python 3.14 upstream deprecations; this project uses
Python 3.12. No warning-producing application API or vendor patch is introduced.
Recheck the vendor contracts when upgrading the integration dependencies.
