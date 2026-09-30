# Choosing an ASGI web server

aiodrf exposes a Django ASGI application, not a server-specific protocol.
Choose a server for lifecycle behavior, operations and measured application
workload. Changing HTTP servers does not make Django's ORM use an async driver
or remove synchronous middleware and serializer work.

## Application entry point

Use aiodrf's application wrapper when the application needs its configured
lifespan context manager or startup/shutdown signals:

```python
# project/asgi.py
import os

from aiodrf.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "project.settings")
application = get_asgi_application()
```

Keep settings selection before application construction. Do not create network
clients during import. Each worker owns a separate lifespan and resource set.
Django's ordinary ASGI application remains suitable when no aiodrf lifespan
features are needed. See [lifespan configuration](lifespan.md).

## Server choices

| Server | Deployment model | Considerations |
| --- | --- | --- |
| Uvicorn | Standalone ASGI process with an optional worker supervisor | Fits the existing Uvicorn/Nginx deployment fixtures; supports lifespan |
| Gunicorn with Uvicorn workers | Gunicorn process management with an ASGI worker implementation | Use `uvicorn-worker`, not the deprecated `uvicorn.workers` import; worker options differ from the standalone CLI |
| Daphne | Django Channels ASGI server | Suitable when its Channels/HTTP/WebSocket stack is already in use; do not assume aiodrf lifespan hooks run |
| Granian | Rust HTTP runtime with Python ASGI workers | Optional `granian` extra; use `--interface asgi`, not WSGI/RSGI or lifespan-disabled `asginl` |

The table compares deployment options, not speed. Uvicorn and Granian are
tested with real HTTP requests: database reads, invalid JSON, multipart bodies,
concurrent calls to other services, cleanup after a Server-Sent Events client
disconnects, and graceful lifespan shutdown. Other server options, platforms,
proxies and WebSocket deployments are not covered.

### Uvicorn

```console
uv pip install 'uvicorn[standard]'
uv run --no-sync uvicorn project.asgi:application --host 127.0.0.1 --port 8000 --workers 1 --lifespan on
```

Use `--reload` for development, not together with multiple workers. Start with
one worker per CPU-limited container and scale after measuring CPU, memory and
database budgets. On a VM, select the process count from those same constraints.
See [Uvicorn deployment](https://uvicorn.dev/deployment/).

### Gunicorn with Uvicorn workers

```console
uv pip install gunicorn uvicorn-worker
uv run --no-sync gunicorn project.asgi:application --worker-class uvicorn_worker.UvicornWorker --bind 127.0.0.1:8000 --workers 2
```

The separate [uvicorn-worker package](https://github.com/Kludex/uvicorn-worker)
provides the worker class. A plain Gunicorn synchronous worker serves WSGI, not
this application. Avoid importing or preloading loop-bound clients in the
supervisor. Validate graceful timeouts and worker replacement with the actual
proxy; standalone Uvicorn CLI options are not automatically Gunicorn options.
This combination is documented but not added to the new Granian socket suite.

### Daphne

```console
uv pip install daphne
uv run --no-sync daphne -b 127.0.0.1 -p 8000 project.asgi:application
```

Daphne's documented protocols are HTTP and WebSocket; it does not drive ASGI
lifespan startup/shutdown. Endpoints depending on aiodrf lifespan state therefore
need a lifespan-capable server. Django AppConfig startup is not an equivalent
place to own asynchronous clients. For Channels routing, let the project own
the outer ASGI router. See [Daphne](https://github.com/django/daphne).

### Granian

```console
uv pip install 'django-aiodrf[granian]'
uv run --no-sync granian --interface asgi --host 127.0.0.1 --port 8000 --workers 1 project.asgi:application
```

During development of this checkout, use `uv pip install -e '.[granian]'`.
No aiodrf setting or contrib adapter is required. `asginl` disables lifespan;
it is not interchangeable with `asgi` when an application owns lifespan resources.

Granian's runtime threads handle server I/O. Its blocking-thread option is not
an application CPU pool for ASGI handlers. Leave thread settings at their defaults
initially; size backpressure and workers against application/DB capacity. HTTP
server threads do not replace Django's thread-sensitive ORM adaptation.
See [Granian's configuration](https://github.com/emmett-framework/granian#workers-and-threads).

## Proxy and resource budgets

Bind privately behind Nginx or a managed ingress. Trust forwarded headers only
from the actual proxy, retain Django's host/HTTPS/CSRF configuration and restrict
direct backend access. Streaming routes need suitable proxy buffering and idle
timeouts. A proxy timeout alone does not cancel a database worker.

Database pools are per process and alias. Count all workers, replicas, rolling
deployment overlap and background jobs; do not multiply processes without
recalculating the [PostgreSQL pool budget](tuning.md#postgresql-connections-under-asgi).
No middleware monkeypatch is required to select any server listed here.

## Tested versions

The servers are tested with the versions in the
[server requirements](../../requirements/servers/requirements.txt), on
CPython 3.14 on Linux; Granian's free-threaded build and Windows are
not tested. [Deployment behaviour](deployment-validation.md) describes
multi-worker Uvicorn behind Nginx and PostgreSQL.
