# Django built-in services

One Django 6.1 application combines admin, authentication URLs, sessions,
messages, templates/humanize, sites/flatpages/redirects, sitemaps/feeds, forms, email, async
signals, model defaults, generated fields, transaction callbacks and cache
backends with aiodrf views. Django continues to own these services.

## Run with Docker

From the repository root:

```console
docker compose -f examples/compose.yaml up --build --wait django-builtin
docker compose -f examples/compose.yaml exec django-builtin python manage.py check
```

The API is available on `http://127.0.0.1:8119`. See
[container setup](../CONTAINERS.md) for port overrides, required services,
optional profiles, tests and data retention. The commands below run the same
application directly with uv.

## Run

```console
uv venv
uv pip install --python .venv/bin/python -r pyproject.toml --group test -e ../..
uv run --no-sync python manage.py migrate
uv run --no-sync python manage.py createsuperuser
uv run --no-sync uvicorn project.asgi:application --host 127.0.0.1 --port 8119
```

Configure `EXAMPLE_ALLOWED_HOSTS` for another host. Settings and credentials are
local development defaults, not a deployment configuration. Admin and the
standard login view share Django's session/CSRF middleware; aiodrf does not
replace the authentication flow. Create flat pages, redirects and site records
through admin. The mail backend records messages in process; no SMTP delivery
is performed.

## Requests

```console
curl -X POST http://127.0.0.1:8119/entries/ -H 'Content-Type: application/json' -d '{"title":"Example"}'
curl http://127.0.0.1:8119/template/
curl http://127.0.0.1:8119/feed/
curl http://127.0.0.1:8119/sitemap.xml
curl -X POST http://127.0.0.1:8119/cache/ -H 'Content-Type: application/json' -d '{"value":"Example"}'
curl http://127.0.0.1:8119/cache/
curl -X POST http://127.0.0.1:8119/contact/ -H 'Content-Type: application/json' -d '{"email":"reader@example.org","message":"Example"}'
curl -X POST http://127.0.0.1:8119/signals/ -H 'Content-Type: application/json' -d '{"value":"Example"}'
```

Visit `/accounts/login/`, then `/session/`, to inspect authenticated session,
locale and message state. `total` is computed by the database; the create
response uses the saved model. The cache callback runs after the explicit
save transaction commits. Public write endpoints are demonstration contracts;
apply application authentication, authorization and throttling before deployment.

## Cache profiles

Select `EXAMPLE_CACHE` before starting the process:

| Value | Backend | Preparation |
| --- | --- | --- |
| locmem | Django LocMemCache | Default; per-process data |
| dummy | Django DummyCache | Intentionally stores nothing |
| file | Django FileBasedCache | Private `.cache` directory; never expose it through a web server |
| database | Django DatabaseCache | Run `EXAMPLE_CACHE=database uv run --no-sync python manage.py createcachetable` |
| redis | Django RedisCache | Dedicated Redis database via EXAMPLE_REDIS_URL |
| django-redis | django-redis | Same service requirement; vendor backend |
| pymemcache | Django PyMemcacheCache | Memcached via EXAMPLE_MEMCACHED |
| pylibmc | Django PyLibMCCache | Native libmemcached development headers, then `uv pip install --python .venv/bin/python -r pyproject.toml --group test --extra pylibmc -e ../..` |

```console
EXAMPLE_CACHE=database uv run --no-sync python manage.py createcachetable
EXAMPLE_CACHE=database uv run --no-sync uvicorn project.asgi:application --host 127.0.0.1 --port 8119
EXAMPLE_CACHE=redis EXAMPLE_REDIS_URL=redis://127.0.0.1:6380/13 uv run --no-sync uvicorn project.asgi:application --host 127.0.0.1 --port 8119
```

Use dedicated local services, not production cache databases. The sample key
prefix prevents application key collisions but does not make a shared Redis
database safe for administrative commands. The example never flushes a remote
cache. Eviction, backend atomicity and cross-process visibility are properties
of the selected backend, not aiodrf.

## Tests and boundaries

```console
uv run --no-sync pytest -q
# Explicit remote backend checks; use dedicated local services.
EXAMPLE_TEST_CACHE_BACKENDS=redis,django-redis uv run --no-sync pytest -q
EXAMPLE_TEST_CACHE_BACKENDS=pymemcache,pylibmc uv run --no-sync pytest -q
```

Tests cover four service-free cache backends, generated output, commit callback,
form errors, local mail, async signals, template rendering and authentication
routes, feeds and sitemaps through ASGI. Redis and Memcached profiles require their actual service;
configuration examples alone are not proof of server availability. PostgreSQL
locking and native database behavior have dedicated projects and test sessions.
GIS/PostGIS and Django's complete administration UI are not reimplemented by
this example. See the [catalogue](../README.md) and
[Django integration guide](../../docs/guides/drf-integration.md).
