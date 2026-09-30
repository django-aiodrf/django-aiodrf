# Managed ASGI resources

Use a Python async context manager for resources that must be opened, used and
closed on the same event loop. It is an opt-in wrapper around Django's ASGI
application, not a middleware patch, a dependency-injection container or a
replacement for Django's application registry.

## Configure a typed resource

For example, an HTTP connection pool (HTTPX is the application's dependency, not
aiodrf's):

```python
# project/lifecycle.py
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx


@dataclass(frozen=True, slots=True)
class Resources:
    http: httpx.AsyncClient


@asynccontextmanager
async def lifespan() -> AsyncGenerator[Resources, None]:
    async with httpx.AsyncClient(timeout=5.0) as http:
        yield Resources(http=http)
```

Add the setting to the existing `AIODRF` dictionary:

```python
AIODRF = {
    "LIFESPAN": "project.lifecycle.lifespan",
}
```

Use the wrapper in the project's `asgi.py`, after its `DJANGO_SETTINGS_MODULE`
setup:

```python
from aiodrf.asgi import get_asgi_application

application = get_asgi_application()
```

The setting accepts `None` (the default), a dotted path or a callable. It names a
factory, not a context manager: each lifespan connection calls it anew, with no
arguments. Configuration comes from Django settings and imports; there is no
FastAPI-style app object.

An explicit keyword overrides the setting, and an explicit `None` disables the
configured context while keeping the signals:

```python
application = get_asgi_application(lifespan=lifespan)
application_without_context = get_asgi_application(lifespan=None)
```

`LifespanApplication(application, lifespan=lifespan)` wraps another ASGI
application directly and does not read settings. Only the root wrapper should
own the lifecycle; do not wrap the same factory again at nested routing layers.

## Access from a view

```python
from aiodrf.asgi import get_lifespan_state
from aiodrf.response import Response
from aiodrf.views import APIView
from project.lifecycle import Resources


class Status(APIView):
    async def get(self, request):
        resources = get_lifespan_state(request, Resources)
        response = await resources.http.get("https://service.example/health")
        response.raise_for_status()
        return Response(response.json())
```

`get_lifespan_state(request, Resources)` returns `Resources`, statically and at
runtime, for Django and DRF requests. The public alias
`LifespanFactory[Resources]` is `Callable[[], AbstractAsyncContextManager[Resources]]`.
The type checker validates a factory passed explicitly; a dotted path in the
settings cannot carry its return type into the views, so the getter checks the
type and raises `ImproperlyConfigured` on a mismatch.

The expected type must support `isinstance`: a dataclass or a concrete class,
not a `TypedDict`, a parameterized generic such as `dict[str, str]` or a
protocol without runtime checks. A frozen dataclass does not make the client in
it thread-safe: use the client's async API on its own loop. Keep request-,
user- and tenant-specific mutable state out of the shared resource.

## Compose multiple resources

Use one context for related clients. `AsyncExitStack` closes resources in reverse
entry order and unwinds already-entered resources if a later initialization
fails. This uses the standard library, not an additional container or global
registry:

```python
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

import httpx

from aiodrf.contrib.async_cache import AsyncCache, cache_lifespan


@dataclass(frozen=True, slots=True)
class Resources:
    http: httpx.AsyncClient
    cache: AsyncCache


@asynccontextmanager
async def lifespan() -> AsyncGenerator[Resources, None]:
    async with AsyncExitStack() as stack:
        http = await stack.enter_async_context(httpx.AsyncClient(timeout=5))
        cache = await stack.enter_async_context(cache_lifespan("native"))
        yield Resources(http=http, cache=cache)
```

Configure the [native cache alias](async-cache.md#backend-configuration) before
using this example. In a view, obtain `get_lifespan_state(request, Resources)`
once and reuse its members. Client construction may be lazy: entering a context
does not necessarily contact a server. If availability is a startup requirement,
perform an explicit bounded readiness check before yielding. Otherwise handle
failures during use according to the application's policy.

### Request-path overhead

The wrapper only distinguishes lifespan scopes from other ASGI connections.
HTTP and WebSocket scopes are passed to the wrapped application unchanged;
WebSocket support still depends on that application. It does not enter resource
contexts, dispatch lifecycle signals or acquire resource locks for each request.
`get_lifespan_state()` performs a state lookup and liveness/type checks without
creating fallback dictionaries or consulting global settings.

`__call__` remains a coroutine function for ASGI server autodetection. Replacing
it with a synchronous callable returning a coroutine can change how older
servers classify the application; avoiding a coroutine allocation is not worth
that compatibility change. Tune downstream I/O, pool capacity and middleware
using [application measurements](performance.md), not an isolated wrapper timing.

## Lifetime and failure contract

The managed sequence is:

1. Enter the context and publish its yielded resource, if it is not `None`.
2. Send `asgi_startup`, then `lifespan.startup.complete`.
3. Serve HTTP requests using the resource on that event loop.
4. On the server's shutdown message, send `asgi_shutdown`.
5. Invalidate request-state access and exit the context; only then send
   `lifespan.shutdown.complete`.

Sync and async signal receivers are dispatched as Django dispatches them. A
receiver failure produces the phase's `.failed` message with its traceback,
after managed cleanup; a context that suppresses the exception cannot turn the
phase into a success. If cleanup raises too, the traceback keeps the exception
chain. Cancelling the lifespan task after entry propagates even if the context
suppresses it or cleanup raises.

Until `__aenter__` returns, partial initialization is the context's own
business. Use nested `async with` blocks or `AsyncExitStack` for dependent
resources, so that an inner failure closes what was opened before. The wrapper
cannot close a client the application allocated and lost before yielding it.

Signals are notifications, not an ordered resource stack. Without a factory
they behave as before; with one, they run inside it and, unlike
[FastAPI's event/lifespan choice](https://fastapi.tiangolo.com/advanced/events/),
are not disabled. Globally connected receivers get events from each wrapper,
with the wrapper class as sender. Put application-specific resources in the
per-application context, and do not let a receiver close a resource the
context owns.

There are no global resource instances, context-variable tricks or cached
entered contexts. The `scope['state']['aiodrf.lifespan']` key holds a private
lifetime marker; use the getter, not its layout. Request copies share the marker
and stop exposing the resource when cleanup starts. Invalidation also releases
the marker's reference to the resource: retaining an old request does not retain
its closed client through aiodrf's state marker. References stored separately by
application code remain the application's responsibility. Other libraries' state keys
are left alone; a conflicting key fails startup instead of being overwritten.

The server must supply and propagate `scope['state']` when the context yields
a resource; otherwise startup fails and the entered context closes. A context
that yields `None` needs no state support. ASGI defines lifetimes per event loop
and shallow request-state copies, so each worker opens its own clients. See the
[ASGI lifespan specification](https://asgi.readthedocs.io/en/latest/specs/lifespan.html).

## Deployment limits

- The ASGI server must run the lifespan protocol. WSGI `runserver`, management
  commands and a server with lifespan disabled do not enter the web application's
  lifespan; the getter then raises instead of opening a pool on the first request.
  An [AsyncCommand](management-commands.md#lifespan-resources) can explicitly enter
  its own resource context with `lifespan=True`.
- `get_asgi_application()` resolves settings after Django initializes. System
  checks import and validate the callable but never call it, so imports must do
  no startup I/O of their own. A running application keeps its factory when
  `override_settings` changes later.
- Do not open loop-bound resources in `AppConfig.ready()`, which is also invoked
  by management commands. Use it for registration; see
  [Django's application initialization guidance](https://docs.djangoproject.com/en/6.1/ref/applications/#django.apps.AppConfig.ready).
- Startup runs per worker: it is not a migration, leader election or job runner.
  Background tasks it starts are stopped, cancelled and awaited in the context;
  a `TaskGroup` does not stop an endless task on normal exit.
- Cleanup is cooperative. A hard kill or repeated cancellation can interrupt
  it; set shutdown budgets on the server and test the resources' behavior.
- Django's HTTP handler, database, cache, session and file storage still use
  threads, and there is no WebSocket handling.

## django-asgi-lifespan

[django-asgi-lifespan](https://github.com/illagrenan/django-asgi-lifespan)
is another lifespan owner: its handler replaces Django's, and it collects
context managers registered from `AppConfig.ready()` and exposes their state as
`request.state`. Use one of the two per ASGI root, not both. Wrapped in
`LifespanApplication`, its handler only ever receives HTTP scopes: its context
managers are never entered, startup still succeeds, and its views fail at
request time. Its handler is Django's and wraps no other application, so the
reverse nesting is not possible either.

Moving to aiodrf:

| django-asgi-lifespan | aiodrf |
| --- | --- |
| `register_lifespan_manager(cm)` in `AppConfig.ready()` | one `AIODRF["LIFESPAN"]` factory that enters each resource in an `AsyncExitStack` ([above](#compose-multiple-resources)) |
| `request.state["client"]` | `get_lifespan_state(request, Resources).client` |
| `django_asgi_lifespan.signals.asgi_startup` / `asgi_shutdown` | `aiodrf.signals.asgi_startup` / `asgi_shutdown`: different signals, so receivers are connected again |

The differences are in the failure contract above: aiodrf enters the
resources before the startup signal and in a fixed order, closes the ones
already entered when startup fails, runs shutdown receivers before it closes
the resources, fails startup when the server provides no `scope["state"]`, and
refuses a request that kept its state past shutdown.

## Tests

`aiodrf.test.lifespan()` runs the lifespan around a block as an ASGI server
would: it enters `AIODRF["LIFESPAN"]` (or the factory it is given), sends
`asgi_startup`, and on exit sends `asgi_shutdown` and closes the resources. It
yields the server's lifespan state; a test client given that state puts a copy
of it into every request, so `get_lifespan_state()` works in the views:

```python
from aiodrf.test import AsyncAPIClient, lifespan


async def test_status():
    async with lifespan() as state:
        client = AsyncAPIClient(lifespan=state)
        response = await client.get("/status/")
        assert response.status_code == 200
```

A failed startup or shutdown raises `RuntimeError` with the application's
report. After the block, the state is invalidated as at a server's shutdown.
`AsyncAPIRequestFactory(lifespan=state)` does the same for views called
directly.

The client still uses Django's async test handler, not the configured ASGI
root. For an end-to-end test of the root itself (its middleware, a server's
view of the application), use `asgi-lifespan` with HTTPX:

```python
import httpx
from asgi_lifespan import LifespanManager

from project.asgi import application


async def test_status():
    async with LifespanManager(application) as manager:
        transport = httpx.ASGITransport(app=manager.app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            response = await client.get("/status/")
            assert response.status_code == 200
```

Use `manager.app`, which propagates state, not the original application; HTTPX's
ASGI transport alone does not start or stop the lifespan. Both packages are test
dependencies of your project. See
[asgi-lifespan state handling](https://github.com/florimondmanca/asgi-lifespan#accessing-state)
and [HTTPX lifecycle guidance](https://www.python-httpx.org/advanced/transports/#asgi-startup-and-shutdown).

Management commands, including schema generation with drf-spectacular, never
start the lifespan factory, and the generated schema is the same before, during
and after the lifespan. If a view chooses its serializer from a lifespan
resource, give it a fallback for schema generation; see the
[ecosystem guide](ecosystem.md#offline-schema-and-lifespan).
