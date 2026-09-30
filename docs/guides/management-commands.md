# Async management commands

Django runs management commands synchronously and has no async variant
([ticket #31793](https://code.djangoproject.com/ticket/31793) was closed with
"use `asyncio.run()`"). `aiodrf.management.AsyncCommand` keeps Django's
command machinery and runs only the handler on an event loop.

## Command implementation

```python
# app/management/commands/refresh_prices.py
from aiodrf.management import AsyncCommand

from app.models import Product


class Command(AsyncCommand):
    help = "Refresh the prices of every product."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)

    async def ahandle(self, *args, limit, **options):
        count = 0
        async for product in Product.objects.all()[:limit]:
            await product.refresh_price()
            count += 1
        self.stdout.write(f"{count} prices refreshed")
```

`async def handle()` works the same way, and so does an `async def handle()`
inherited from a mixin. Either way the handler is awaited: Django never
receives an unawaited coroutine.

Everything else is Django's and behaves as for any command: arguments,
`self.stdout` and `self.stderr`, a returned string written to stdout
(wrapped in `BEGIN;`/`COMMIT;` with `output_transaction = True`),
`CommandError(returncode=...)` and `sys.exit()` statuses, `call_command()`.
System checks and migration checks run synchronously in `execute()`, before
the loop starts, and `skip_checks` is honoured.

## Event loop and database connection ownership

The handler runs on a new event loop driven by `async_to_sync`. The
difference is where the async ORM's synchronous work (`acount()`, `aget()`,
... hand the query to `sync_to_async`) runs. Under `async_to_sync` it runs in
the command's own thread; under `asyncio.run()` it runs in asgiref's
executor thread. Measured with Django 6.1 and asgiref 3.12:

- `manage.py` runs `connections.close_all()` in the calling thread after the
  command. Under `asyncio.run()` the connection the async ORM opened
  belongs to the executor thread: it stays open and is finalized with
  `ResourceWarning: unclosed database`. Under `async_to_sync` it is the
  command's connection, and Django closes it.
- In a synchronous `TestCase`, the command's queries under `asyncio.run()`
  use another connection than the test's transaction: they do not see
  `setUp()`'s rows, and SQLite fails with "database table is locked". Under
  `async_to_sync` they run inside the test's transaction.

## Signals

`async_to_sync` runs the loop in another thread, so on its own a Ctrl-C
would interrupt only the waiting main thread and the coroutine would carry
on. While the handler runs, `AsyncCommand` replaces Python's default SIGINT
and SIGTERM handlers (as `asyncio.run()` does for SIGINT) with one that
cancels the handler's task:

- SIGINT: the task is cancelled, its `except CancelledError`/`finally`
  cleanup runs (async and thread-sensitive work alike), and
  `KeyboardInterrupt` is raised, as without aiodrf.
- SIGTERM: the same cancellation, then `SystemExit(143)`: 128 + 15, the
  status a shell reports for a process SIGTERM terminated. Unlike being
  killed by the signal, the exit still runs Django's cleanup, such as
  closing the connections.

A second signal gets the handler that was there before: the process stops
without waiting for the cleanup. A handler that catches `CancelledError` and
returns finishes the command normally, as under `asyncio.run()`.

Only Python's defaults are replaced. A handler the project installed, or
`SIG_IGN` (`nohup`, background jobs), is left alone. Outside the main thread,
where Python cannot install signal handlers, no handler is installed.

## Lifespan resources

A command can use the resources of `AIODRF['LIFESPAN']` (see
[Managed ASGI resources](lifespan.md)):

```python
from project.lifecycle import Resources


class Command(AsyncCommand):
    lifespan = True

    async def ahandle(self, *args, **options):
        resources = self.get_lifespan_state(Resources)
        response = await resources.http.get("https://example.com/prices")
        ...
```

The context manager is entered once, on the command's loop, around the
handler, and exits after it with the handler's exception, if any. A failure
while entering it propagates and the handler does not run.
`get_lifespan_state()` checks the type as `aiodrf.asgi.get_lifespan_state()`
does, and raises `ImproperlyConfigured` outside the handler, for a command
without `lifespan = True`, or for another type. `lifespan = True` without
the setting is `ImproperlyConfigured` too.

The default is `lifespan = False`: most commands need no HTTP pools or
clients, and entering them costs their startup time.

The `asgi_startup` and `asgi_shutdown` signals are not sent: their receivers
expect an ASGI scope.

## Native connections

With `django_async_backend` in `INSTALLED_APPS` (see
[Native async ORM](async-backend.md)), the handler runs in
`async_new_connection()`: its native connections belong to the command's
loop and are closed before the loop ends.

## Calling a command from async code and in tests

`call_command()` of an `AsyncCommand` in a thread whose event loop is
running raises `SynchronousOnlyOperation`, the exception Django's ORM raises
in the same situation, naming the replacement:

```python
from aiodrf.management import acall_command

await acall_command("refresh_prices", "--limit", "10", stdout=out)
```

`acall_command()` runs `call_command()` in the thread synchronous work goes
to, as the async ORM does. In an async test of a Django `TestCase`, that is
the test's thread, so the command sees the test's transaction.

## django-typer

django-typer has no async support: a `TyperCommand` whose `handle` is
`async def` returns its coroutine unawaited. With `AsyncCommand` first, the
coroutine runs on the command's loop:

```python
from django_typer.management import TyperCommand

from aiodrf.management import AsyncCommand


class Command(AsyncCommand, TyperCommand):
    async def handle(self, name: str, shout: bool = False): ...
```

`AsyncCommand`'s default `ahandle()` calls the next `handle()` in the MRO,
django-typer's, which parses the arguments and returns the coroutine, and
awaits it. Calling the command object directly (`command(...)`, django-typer's
shortcut) bypasses `execute()` and still returns the coroutine.

## Limits

- There are no async variants of `AppCommand` and `LabelCommand`.
- The loop is asyncio's default, not uvloop: `async_to_sync` starts it with
  `asyncio.run()` and takes no loop factory, and Python 3.14 deprecates the
  event loop policy API that installing uvloop globally relies on.
