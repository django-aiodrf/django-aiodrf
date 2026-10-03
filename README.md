# django-aiodrf

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/django-aiodrf/django-aiodrf/main/assets/aiodrf-logo/svg/aiodrf-logo-dark-bg.svg">
  <img src="https://raw.githubusercontent.com/django-aiodrf/django-aiodrf/main/assets/aiodrf-logo/svg/aiodrf-logo.svg" alt="django-aiodrf" width="420" height="93">
</picture>

Awaitable views, serializers and policy hooks for Django REST framework.

aiodrf extends DRF's classes and preserves its request, validation and response
contracts. Migrate endpoints incrementally: async implementations are awaited;
synchronous hooks that may perform I/O execute in a thread-sensitive worker.
Existing DRF serializers, routers, authentication and permission classes remain
usable.

The package is alpha and its API may change; [CHANGELOG.md](https://github.com/django-aiodrf/django-aiodrf/blob/main/CHANGELOG.md) lists
the releases.
The distribution name is `django-aiodrf`; imports use `aiodrf`.

## Installation

Optional JSON transports use `django-aiodrf[pydantic]` or
`django-aiodrf[orjson]`. Select fastdrf’s parser and renderer classes explicitly
in `REST_FRAMEWORK` or on the view; see the
[JSON transport guide](https://github.com/django-aiodrf/django-aiodrf/blob/main/docs/guides/msgspec-pydantic.md#3-json-renderer-and-parser).

For this development checkout:

```console
uv venv --python 3.12
uv pip install -e .
```

Install with uv:
```console
uv add django-aiodrf
```

Install with pip:
```console
pip install django-aiodrf
```


Optional dependencies are selected explicitly, for example
`django-aiodrf[filter,spectacular]`.

Add the application to Django settings:

```python
INSTALLED_APPS = [
    # Your existing Django applications.
    "rest_framework",
    "aiodrf",
]
```

Keep authentication, permission, parser and renderer configuration in
`REST_FRAMEWORK`. Optional aiodrf behavior is configured through `AIODRF`;
the default configuration requires no optimization settings. The serializer
optimizations (compiled msgspec, pydantic or Python output, input recognition,
field caching) come from [django-fastdrf](https://github.com/ctolon/django-fastdrf),
a dependency of aiodrf, and are configured through its `FASTDRF` setting.

## Basic usage

```python
# project/urls.py
from django.urls import path

from aiodrf.response import Response
from aiodrf.views import APIView


class StatusView(APIView):
    async def get(self, request):
        return Response({"status": "ok"})


urlpatterns = [
    path("status/", StatusView.as_view()),
]
```

Serve the project with an ASGI server. aiodrf's `aiodrf.asgi.get_asgi_application()`
is needed only for its optional features, such as the
[lifespan context manager](https://github.com/django-aiodrf/django-aiodrf/blob/main/docs/guides/lifespan.md).

For model APIs, aiodrf provides `ModelViewSet`, generic views and serializers
with async methods. Plain DRF serializers can also be used through
`aiodrf.aio.is_valid()`, `save()` and `data()`. Supported extension hooks have
explicit sync/async pairs such as `get_queryset()` / `aget_queryset()`.

## Capabilities and boundaries

- DRF-style views, routers, validation, authentication, permissions and throttling.
- Awaitable serializer operations and compatible synchronous extension hooks.
- Typed lifespan resources, streaming responses and conditional requests.
- Optional msgspec/Pydantic serializers, compiled serializer backends
  (msgspec, Pydantic, or plain Python without a dependency) and selective
  field-copy optimizations.
- Tested integrations for filtering, OpenAPI, object permissions, caching,
  background tasks and SQL/NoSQL access.

Django's async ORM methods still run the database driver synchronously, in a
worker thread; `await` alone does not make database access natively
asynchronous. aiodrf keeps synchronous code it cannot verify off the event
loop. Compiled serializers, the native database backend and the compatibility
layers are opt-in, and their guides describe their limits. How much faster an
async API is depends on the application and its deployment.

## Compatibility

Python 3.12, 3.13 and 3.14, including a separate free-threaded test session:

| Django | DRF |
| --- | --- |
| 5.2 | 3.16, 3.17, 3.18 |
| 6.0 | 3.17, 3.18 |
| 6.1 | 3.18 |

Tests run on SQLite and PostgreSQL, against DRF's own test suite and with a
selection of third-party packages; the [ecosystem guide](https://github.com/django-aiodrf/django-aiodrf/blob/main/docs/guides/ecosystem.md)
lists them.

## Documentation

The [aiodrf documentation](https://django-aiodrf.github.io/django-aiodrf/) covers migration, configuration,
the API reference and the [examples](https://github.com/django-aiodrf/django-aiodrf/blob/main/examples/README.md), which run locally with
uv or with Docker Compose.

Use the [Django documentation](https://docs.djangoproject.com/en/stable/) for
models, middleware, settings and deployment, and the
[Django REST framework documentation](https://www.django-rest-framework.org/)
for DRF's API. aiodrf's guides describe what aiodrf adds and do not repeat
those references.

## Development

```console
uv pip install -e . --group dev
uv run --no-sync pytest
uv run --no-sync nox -s lint typecheck docs
```

The [contribution guide](https://github.com/django-aiodrf/django-aiodrf/blob/main/CONTRIBUTING.md) describes the development workflow,
the test sessions and the code standards.

## Credits

django-aiodrf builds on the foundations of Django REST framework, Django, and asgiref.

We gratefully acknowledge the DRF team and contributors for their API design, implementation, documentation, and extensive compatibility test suite, as well as the Django and asgiref contributors for the request-handling and asynchronous infrastructure on which this project relies.

Portions of source code adapted from these projects retain the applicable copyright notices, license terms, and attribution as required by their respective licenses.
Native async cache backends are distributed as `aiodrf-async-cache`; lifespan
resources, signals and the testing helper as `aiodrf-asgi-lifespan` (the
`django-aiodrf[lifespan]` extra). Their source packages are under `forks/`.
Use the top-level `DJANGO_LIFESPAN` setting for the resource factory.
