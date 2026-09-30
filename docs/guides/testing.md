# Testing aiodrf applications

Test response contracts, authorization, database effects and cleanup, not
only the coroutine status of a handler. Use ordinary Django/DRF assertions
for synchronous endpoints and the async client for async application paths.

## Regression-driven changes

For a feature or bug fix, start with a test of the observable contract and confirm
its failure before changing the implementation. After the fix, run that test and
the affected integration cases. Compare with ordinary DRF when preserving its
validation, response or override behavior. Test default and opted-in execution
separately; a fast path must not bypass authorization or cleanup.

The project [TDD policy](../../CONTRIBUTING.md#test-driven-development) specifies
the evidence expected in a PR, including checks that could not be run. For an
application, retain the same regression discipline without copying the framework's
entire version matrix. Documentation-only changes use documentation contracts.

## pytest configuration

Install pytest, pytest-django and pytest-asyncio in the application's
development dependency group. A minimal pyproject.toml configuration is:

```toml
[tool.pytest.ini_options]
DJANGO_SETTINGS_MODULE = "project.settings"
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
filterwarnings = ["error"]
```

Use a dedicated test database. pytest-django refuses database access without
the appropriate fixture or marker. Never select a production database for
test runs, migrations or example applications.

## HTTP contracts

```python
import pytest
from aiodrf.test import AsyncAPIClient


@pytest.mark.django_db(transaction=True)
async def test_article_creation():
    client = AsyncAPIClient()
    response = await client.post("/articles/", {"title": "Example"}, format="json")
    assert response.status_code == 201
    assert response.data["title"] == "Example"
```

`transaction=True` is appropriate when worker threads or independent requests
must observe committed data. Django `TestCase` and pytest's normal `db` fixture
provide useful rollback isolation but are not substitutes for transaction,
locking or cross-connection tests. Async ORM entry points still need database
permission from pytest-django.

`force_authenticate()` isolates permissions and view behavior. Add separate
tests with actual credentials to exercise authentication, expiry, malformed
headers and the 401 challenge. Use
`AsyncAPIClient(enforce_csrf_checks=True)` for session-authenticated mutations.
Verify object permissions on list and detail endpoints and ensure denied
writes leave the database unchanged.

`AsyncAPIClient` and `AsyncAPIRequestFactory` accept DRF's forms for
headers: `HTTP_*` keywords and `headers=`, in the constructor, per request
and in `credentials()`. They differ from DRF's `APIClient` in two ways:

- A request's own header takes precedence over `credentials()`; in DRF's
  client the credentials win. The constructor's headers come last.
- `force_authenticate(None)` stops forcing authentication but does not log
  out a session, as DRF's does; call `await client.alogout()` for that.

## Serializer and hook tests

```python
from rest_framework import serializers
from aiodrf import aio


class Input(serializers.Serializer):
    quantity = serializers.IntegerField(min_value=1)


async def test_invalid_quantity():
    serializer = Input(data={"quantity": 0})
    assert not await aio.is_valid(serializer)
    assert serializer.errors["quantity"][0].code == "min_value"
```

For each custom sync/async hook, test the inherited implementation, an override
and a `super()` call. Test both Python and rendered JSON values for compiled
serializers, including invalid input, PATCH, nulls, decimals and dates. An
equivalent ordinary DRF serializer is a useful reference. Do not assert only
that a backend was selected: unsupported cases may correctly fall back to DRF.

## ASGI application and lifespan

```python
import httpx
from asgi_lifespan import LifespanManager
from project.asgi import application


async def test_ready():
    async with LifespanManager(application) as manager:
        transport = httpx.ASGITransport(app=manager.app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            response = await client.get("/ready/")
            assert response.status_code == 200
```

Use `manager.app`, which supplies lifespan state. HTTPX's ASGI transport does
not run lifespan automatically. Its in-process transport does not reproduce
socket backpressure, proxy buffering or the timing of client disconnects; test
those with a real server (see [deployment behaviour](deployment-validation.md)).

## Concurrency and resources

Create independent clients, serializer instances and request context per
concurrent operation. Use barriers/events to reproduce races; do not rely on
arbitrary sleeps. Test cancellation while work is active, verify owned tasks
are joined, and assert clients/generators are closed. Cancellation does not
terminate a synchronous database worker or undo its completed transaction.

Use `aiodrf.test.count_hops()` only for targeted aiodrf scheduling regressions.
It does not count all Django middleware adaptation or database-driver threads.
Use query assertions and a profiler alongside it. Separate latency benchmarks
from functional tests; never make shared CI runners enforce microsecond limits.

## Coverage and warnings

An application can run `pytest --cov=your_app --cov-branch --cov-report=term-missing`.
Choose a threshold based on its own behavior and risk. This repository's
combined library gate is 85%; it includes optional runtime modules rather than
excluding untested integrations. The [contribution guide](../../CONTRIBUTING.md)
explains unit/integration separation and combining reports.

Keep deprecations as errors. Assert intentional compatibility warnings with
`pytest.warns(..., match=...)`; do not disable a warning category globally.
Third-party deprecations must have a bounded version-specific record and a
removal condition. Fix deprecated calls in application code instead of hiding
them with `filterwarnings`.
