# Integrating aiodrf into a DRF project

A project can keep its DRF views and add aiodrf views next to them, in the
same URLconf and the same router, without moving everything. This guide says
what the two kinds share and where they differ while both run. Moving an
endpoint from one kind to the other is the
[migration guide](migration-from-drf.md)'s subject; how aiodrf runs a request
is in the [implementation guide](implementation.md).

"DRF view" below means a class from `rest_framework` (`APIView`, generic
views, viewsets, `@api_view`); "aiodrf view" one from `aiodrf`. Everything on
this page holds for a URLconf that mixes both kinds, under Django's synchronous
and asynchronous request handlers.

## Installation

Install a supported Django/DRF pair and add `"aiodrf"` to `INSTALLED_APPS`.
Keep API components in `REST_FRAMEWORK`; database, cache, middleware and storage
configuration remain ordinary Django settings. Introduce async views endpoint
by endpoint. No project-wide class replacement or middleware patch is required.

## 1. Shared configuration and protocols

aiodrf's views subclass DRF's, so they read the same settings and accept the
same classes.

| Component | Behaviour |
| --- | --- |
| `REST_FRAMEWORK` defaults | Authentication, permission, renderer, parser, throttle, pagination, filter, negotiation, metadata and versioning classes are the same objects on both kinds' base classes, read when the class is defined, as in DRF. |
| `EXCEPTION_HANDLER` | Called by both, and read at request time. |
| Sessions and users | A session login authenticates both kinds; `force_authenticate()` on a test client reaches both. |
| Throttle history | DRF's rate throttles use the same cache keys in both kinds: requests to a DRF view count against an aiodrf view with the same scope. |
| Routers | `aiodrf.routers` re-exports DRF's routers. One `DefaultRouter` registers DRF and aiodrf viewsets; its API root lists both. |
| `@api_view` | DRF's decorator for synchronous functions; aiodrf's for `async def` functions, which run on the event loop, and for synchronous ones, which run in a worker thread. |
| Browsable API | Rendered for both kinds, including a DRF view whose serializer has async members. |
| OpenAPI | drf-spectacular documents both kinds in one schema. The same serializer gives the same operations in a DRF and an aiodrf viewset; `spectacular --validate --fail-on-warn` passes over a URLconf that mixes them. |

```python
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from legacy.views import InvoiceViewSet  # rest_framework.viewsets.ModelViewSet
from api.views import SearchViewSet  # aiodrf.viewsets.ModelViewSet

router = DefaultRouter()
router.register("invoices", InvoiceViewSet)
router.register("search", SearchViewSet)

urlpatterns = [path("api/", include(router.urls))]
```

## 2. Serializers across the two kinds

| Serializer | In a DRF view | In an aiodrf view |
| --- | --- | --- |
| Plain DRF, synchronous members only | DRF | Works; validation, save and representation run in the action's worker hop. |
| Plain DRF with `acreate()` or `async def` hooks | DRF does not call `acreate()`: its default `create()` saves the data as sent. DRF calls an `async def validate_<field>()`, `validate()` or `get_<field>()` without awaiting it, so the coroutine object ends up in `validated_data`, the saved row or `.data`. System check `aiodrf.W010` reports these serializers. | aiodrf's generic view awaits them. A `perform_create()`/`perform_update()` written for DRF that calls `save()` raises `ImproperlyConfigured`. A handler of the project's that calls DRF's `is_valid()`, `save()` or `.data` itself gets the coroutines as in a DRF view; await `aiodrf.aio.is_valid()`, `aio.save()` and `aio.data()` instead. |
| aiodrf (`aiodrf.serializers`) | Works: `is_valid()`, `save()`, `create()` and `.data` bridge to the async members through `async_to_sync`. | Works, awaited. |

An aiodrf serializer is the one to share between both kinds when it has async
members (`test_an_aiodrf_serializer_in_a_drf_view`: an async
`validate_<field>`, `acreate()` and an async `SerializerMethodField` method, in
a DRF `ListCreateAPIView`). Each bridge call blocks the DRF view's thread
until its coroutine has finished. The plain DRF serializer cases are
`test_async_creation_of_a_plain_drf_serializer`.

`ATOMIC_SAVE` belongs to the serializer, not the view: an aiodrf serializer's
`save()` runs in `transaction.atomic()` in a DRF view too, while a plain DRF
serializer in a DRF view keeps DRF's autocommit
(`test_atomic_save_follows_aiodrf_serializers_into_drf_views`).

## 3. Policies and shared code

A permission, authentication or throttle class written for DRF works in both
kinds. In an aiodrf view its synchronous method runs in a worker thread (one
hop for the permissions and throttles together), unless it is declared
`@async_safe`; a DRF view never hops
(`test_a_sync_permission_costs_a_hop_in_aiodrf_only`).

A class that needs async work and is used by both kinds has two options:

```python
from rest_framework.permissions import BasePermission


class IsMember(BasePermission):
    # Both members: DRF views call the first, aiodrf views await the second.
    def has_permission(self, request, view):
        return Membership.objects.filter(user=request.user).exists()

    async def ahas_permission(self, request, view):
        return await Membership.objects.filter(user=request.user).aexists()
```

or subclass `aiodrf.permissions.BasePermission` and write only
`ahas_permission`: its `has_permission` runs the async member through
`async_to_sync` when DRF calls it. `aiodrf.authentication.BaseAuthentication`
(`aauthenticate`) and `aiodrf.throttling.BaseThrottle` (`aallow_request`) do
the same.

| Class | DRF view | aiodrf view | Test |
| --- | --- | --- | --- |
| DRF base, `has_permission` and `ahas_permission` | `has_permission` | `ahas_permission` | `test_a_permission_with_both_members` |
| DRF base, `ahas_permission` only | **allows**: DRF calls the base `has_permission`, which returns `True` | awaited | `test_an_async_only_permission_on_drfs_base_allows_in_drf_views` |
| aiodrf base, `ahas_permission` only | bridged | awaited | `test_an_async_only_permission_on_aiodrfs_base_denies_in_both` |
| aiodrf bases, `aauthenticate` / `aallow_request` only | bridged | awaited | `test_async_only_authentication_and_throttles_on_aiodrfs_bases` |

`manage.py check` warns (`aiodrf.W008`) when a DRF view in the URLconf uses a
class on DRF's base with only the async member: a permission, which then
allows, or an authentication or throttle class, whose DRF default raises
`NotImplementedError`. The same holds for a class deriving from one of DRF's
policies (`AllowAny`, `IsAuthenticated`, ...) and adding only the async
member: DRF views run the inherited sync member, never the async one.

A mixin written for DRF (`get_queryset()` scoped to `request.user`,
`perform_create()` calling `serializer.save(owner=request.user)`) serves a
DRF viewset and an aiodrf viewset unchanged. In the aiodrf one it runs inside
the action's single hop (`test_a_mixin_written_for_drf_serves_both`,
`test_the_mixin_costs_aiodrf_one_hop`). Its async hooks do not reach DRF
views: DRF calls `get_queryset()`, never `aget_queryset()`
(`test_drf_views_ignore_async_hooks`). Keep a shared mixin synchronous, or give
it both members of each pair.

## 4. Tests

| Tool | DRF views | aiodrf views |
| --- | --- | --- |
| `APIClient` (DRF's; `aiodrf.test` re-exports it) | Django's synchronous test handler | the same test handler adapts the async view with `async_to_sync` |
| `aiodrf.test.AsyncAPIClient` | Django's asynchronous test handler adapts the sync view in a worker | on the event loop; does not start application lifespan |
| `APIRequestFactory` | call the view | the view is a coroutine function: `async_to_sync(view)(request)` |
| `aiodrf.test.AsyncAPIRequestFactory` | — | `await view(request)` |
| `aiodrf.test.count_hops()` | records nothing | records aiodrf's hops |

Either client can test either kind, and `force_authenticate` works with both.
To run the same test through both clients, parametrize it over them.

Use the configured ASGI application with HTTPX and a lifespan manager when
testing application startup/state. Use real-server tests for transport
backpressure and disconnect timing; see [application testing](testing.md).

## 5. WSGI and ASGI

| | WSGI | ASGI |
| --- | --- | --- |
| DRF view | as before | Django runs it in a worker thread |
| aiodrf view | works: Django runs it with `async_to_sync`, so its async code has an event loop | on the event loop |

Both rows are `test_function_views_of_both_decorators` (whether an event loop
is running in the view) under each transport; an aiodrf viewset costs the same
hops under both (`test_an_aiodrf_viewset_costs_one_hop_under_both_transports`).
Under WSGI a worker still serves one request at a time, so an aiodrf view
gains no concurrency there, and streaming responses are consumed whole before
they are sent ([implementation guide](implementation.md#6-streaming-responses-and-the-lifespan)).
aiodrf has not measured WSGI capacity. Serve with ASGI once aiodrf endpoints
matter; DRF views keep working there, as the tests show. See Django's
[async guide](https://docs.djangoproject.com/en/6.0/topics/async/) for how it
adapts views and middleware.

Middleware is shared. Under ASGI, Django adapts middleware that is not
`async_capable` around async views; `manage.py check --deploy --tag
compatibility` lists it (`aiodrf.W005`, [system checks](../reference/checks.md)).

Effects of `AIODRF` settings on DRF views:

| Setting | Effect on DRF views |
| --- | --- |
| `ATOMIC_SAVE` | Applies to aiodrf serializers wherever they are saved, DRF views included; not to plain DRF serializers (section 2). |
| `SERIALIZER_BACKEND` | None: the compiled backends are used by aiodrf views only (`test_the_serializer_backend_applies_to_aiodrf_views_only`). |
| `VALIDATION_UNKNOWN`, `REPRESENTATION_MODE`, `PURE_POLICIES`, `INLINE_RENDERERS` | None: they decide whether aiodrf runs code on the event loop or in a thread. A DRF view runs everything in its own thread. |

## 6. Transactions

`ATOMIC_REQUESTS` still gives each DRF view a request transaction, rolled back
when DRF's exception handler answers an error. Django refuses it for async
views: every request to an aiodrf view raises `RuntimeError`, under WSGI as
well, unless the view is excluded. `aiodrf.W002` reports the setting.

```python
from django.db import transaction

# A view in the URLconf:
urlpatterns = [path("search/", transaction.non_atomic_requests(SearchView.as_view()))]


# A viewset registered with a router: as_view() copies dispatch's marker.
class SearchViewSet(viewsets.ModelViewSet):
    @transaction.non_atomic_requests
    async def dispatch(self, request, *args, **kwargs):
        return await super().dispatch(request, *args, **kwargs)
```

An excluded view has no request transaction; `ATOMIC_SAVE` covers its default
save, and the [migration guide](migration-from-drf.md#5-transactions) says
how to make several writes atomic. Tests:
`test_atomic_requests_roll_back_drf_views_only`,
`test_atomic_requests_refuse_async_views`,
`test_a_routed_viewset_opts_out_through_its_dispatch`.

## 7. Pitfalls

- **`async def` handlers in a DRF view.** DRF calls the handler, gets a
  coroutine and fails with its `Expected a Response` assertion
  (`test_an_async_handler_in_a_drf_view_fails`). Async handlers belong in
  aiodrf views.
- **Async hooks on a DRF view.** `aget_queryset`, `aperform_create` and the
  other `a...` members are aiodrf's; a DRF view ignores them (section 3).
- **An async-only permission on DRF's base class** allows every request in a
  DRF view (section 3).
- **A plain DRF serializer with `acreate()`**: skipped in a DRF view, refused
  with `ImproperlyConfigured` by a DRF-style `perform_create()` in an aiodrf
  view (section 2). Inherit from `aiodrf.serializers`, or write
  `aperform_create()`.
- **Calling async code from a DRF view** works through `async_to_sync`
  (`test_a_drf_view_can_call_aio_through_async_to_sync` calls
  `aiodrf.aio.is_valid`), and blocks the view's thread until it returns. An
  aiodrf serializer's synchronous members already do this; in a DRF view,
  call `is_valid()`, `save()` and `.data`, not `aiodrf.aio`.
- **`request.user` in async code.** In an aiodrf view, the default
  authentication has loaded `request.user` before an `async def` handler
  runs (`test_session_authentication_is_shared`). A view that authenticates
  lazily awaits `request.auser()` ([migration guide](migration-from-drf.md#4-async-application-code)).
- **`ATOMIC_REQUESTS`** breaks every aiodrf view that is not excluded
  (section 6).

## 8. Migration order

| Endpoint | Execution location | Execution constraint |
| --- | --- | --- |
| Waits on other services (HTTP APIs, async clients) | aiodrf first | The wait is awaited on the event loop instead of holding a thread. Use the [measurement guide](performance.md) to evaluate the application's I/O workload. |
| Streams (SSE, NDJSON, long responses) | aiodrf, under ASGI | `StreamingResponse` and `EventStreamResponse`; WSGI consumes the stream before answering. |
| Plain ORM CRUD | DRF, until there is a reason | Django's queries still run in a thread: a `ModelViewSet` request costs one hop, and async execution does not raise throughput for every ORM workload ([performance guide](performance.md)). |
| Relies on `ATOMIC_REQUESTS` | DRF, until the transaction is explicit | Section 6. |
| Uses packages not in the [ecosystem guide](ecosystem.md) | DRF, until tested | Their hooks run in a thread, but their behaviour under aiodrf is not checked. |
| Large list output | either; aiodrf for `SERIALIZER_BACKEND` | The compiled backends apply to aiodrf views only ([serializer guide](msgspec-pydantic.md)). |

## Migration verification

Before replacing an endpoint, compare status, headers, payload, validation errors,
authorization, transaction effects and query counts against its DRF implementation.
The [migration guide](migration-from-drf.md) covers the conversion itself.

Synchronous vendor authentication, permission and filter hooks use worker
adaptation, not native async I/O. Object-level filtering must preserve the
authorized queryset on list endpoints as well as detail permission checks.
Queryset optimization cannot replace authorization. Consult the
[ecosystem contracts](ecosystem.md) and [runnable examples](../../examples/README.md)
for each selected package.

For create/update responses, a dedicated read serializer can represent the
saved instance. Preserve context, success headers and saved relation ordering;
do not replace persisted output with uncommitted input. The
[tuning guide](tuning.md) covers independent opt-in optimizations.

Use the [ASGI server guide](web-servers.md) for deployment choices and
[application tests](testing.md) for full ASGI/lifespan and socket-test boundaries.
