# Deployment

An aiodrf project is deployed like any Django project. It needs no patches, no
replacement application registry and no special middleware.

## ASGI

Serve the project with an ASGI server; [choosing an ASGI server](web-servers.md)
compares Uvicorn, Gunicorn with Uvicorn workers, Daphne and Granian. Use an
ASGI server for streaming responses (NDJSON, JSON arrays, Server-Sent Events):
under WSGI, an async iterator can be consumed entirely before the response is
sent.

Django's own `get_asgi_application()` is enough for most projects. Use
`aiodrf.asgi.get_asgi_application()` in `asgi.py` when you need a
[lifespan context manager](lifespan.md) for resources, and install `django-aiodrf[lifespan]`. For signal-only applications, use
`aiodrf_asgi_lifespan.asgi.get_asgi_application()`.
With `AIODRF["REQUEST_THREADS"]` set, it also keeps the threads that run
requests' synchronous code for later requests, instead of starting two threads
per request ([setting](../reference/settings.md#request_threads)).

Run Django's deployment checks with the production settings:

```console
python manage.py check --deploy
python manage.py check --deploy --tag compatibility
```

The [system checks reference](../reference/checks.md) explains every check aiodrf
adds and how to resolve it.

Under ASGI, keep `CONN_MAX_AGE = 0` and use your database backend's connection
pool if you need one. [Deployment behaviour](deployment-validation.md) describes
how aiodrf behaves with several Uvicorn workers behind Nginx and PostgreSQL:
worker reloads and crashes, connection budgets and streaming through the proxy.

## Resources and cancellation

Create clients that belong to an event loop during the lifespan, not at module
import. Give the keys you store in the ASGI state a unique prefix, and handle
servers that do not provide `scope["state"]`.

Prefer the [lifespan context manager](lifespan.md) for resources: it runs around
both startup and shutdown signals and releases what it opened if startup fails.
Signal receivers, by contrast, only notify: each owns its resources, and one
receiver's failure does not undo another receiver's work. Exceptions from
receivers are collected before startup is reported as failed, and cancellation
is never swallowed. Open dependent resources inside the context manager with
`async with` or `AsyncExitStack`, and do not open and close the same client in
both the context manager and a receiver.

A streaming response closes its producer, and the iterator of the middleware
around it, when it completes or the client disconnects. Middleware that wraps a
response iterator must close it as well. Do not catch `CancelledError` to keep
producing. Once part of a JSON array has been sent, the response can no longer
be replaced by a DRF error response.

Cancelling a request does not stop a database operation or task already running
in a worker thread. An `on_commit` callback avoids publishing a task when the
transaction rolls back, but it does not make the database write and the
message delivery atomic.

## Checking serializers before tuning

`aiodrf_inspect_serializers` reports which serializers the msgspec, Pydantic or
python backend can compile, and why the others cannot:

```console
python manage.py aiodrf_inspect_serializers --backend msgspec --format json
python manage.py aiodrf_inspect_serializers --backend pydantic --format json
python manage.py aiodrf_inspect_serializers --serializer project.api.BookSerializer --backend msgspec
python manage.py aiodrf_inspect_serializers --serializer project.api.BookSerializer --serializer project.api.AuthorSerializer --format json
```

Without `--serializer`, the command finds the serializers your views declare,
including `@action(serializer_class=...)` and `as_view(serializer_class=...)`.
Each JSON record lists the paths, HTTP methods and viewset actions that use the
serializer under `usages`. The command does not call `get_serializer_class()`,
so serializers chosen at request time are not found. `--serializer` can be
repeated; it inspects the named classes only, and their `usages` list is empty.

Each serializer is instantiated without a request, so its constructor runs:
inspect only code you trust. A path that cannot be imported or does not name a
DRF serializer raises `CommandError`. To inspect an instance built for a
specific request, call `fastdrf.compiler.report_details(serializer)` or
`fastdrf.inputs.report_input_details(serializer)`. A serializer whose
input can be compiled may still pass a particular payload to DRF for
validation.

To see where a request runs synchronous code in worker threads, wrap it in
`aiodrf.test.count_hops()`: it records the aiodrf function behind each switch,
without logging request data. It does not count the thread switches Django
makes itself (synchronous middleware, signals). Enable it in tests and
diagnostics only, not while measuring throughput.

## Static files

For new deployments use ServeStatic or serve collected static files through
your reverse proxy. `aiodrf.contrib.whitenoise` remains available in aiodrf;
its adapter is planned for future deprecation, without a separate package.
