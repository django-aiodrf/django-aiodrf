# Serializer and representation integrations

One project contains the related field, serializer, router and renderer
examples. Each endpoint has a specific wire format; no global renderer switch
is needed to use a vendor integration.

## Run with Docker

From the repository root:

```console
docker compose -f examples/compose.yaml up --build --wait ecosystem-data
docker compose -f examples/compose.yaml exec ecosystem-data python manage.py check
```

The API is available on `http://127.0.0.1:8121`. See
[container setup](../CONTAINERS.md) for port overrides, required services,
optional profiles, tests and data retention. The commands below run the same
application directly with uv.

## Run

```console
uv venv
uv pip install --python .venv/bin/python -r pyproject.toml --group test -e ../..
uv run --no-sync python manage.py migrate
uv run --no-sync uvicorn project.asgi:application --host 127.0.0.1 --port 8121
uv run --no-sync pytest -q
```

Use `EXAMPLE_ALLOWED_HOSTS` for another hostname. Public writes and the development
key are for local use. Add authentication, object authorization, storage limits
and upload content policies before adapting these endpoints to a deployment.

## Integration map

| Packages | Route | Demonstrated contract |
| --- | --- | --- |
| rest-filters | `/filtered-books/?title=earth&author=ursula` | Validated query parameters, explicit relation lookup and filtering on list/detail endpoints; unknown parameters return 400. |
| django-money, django-phonenumber-field, django-taggit, django-pydantic-field | `/authors/`, `/books/` | Currency-aware amounts, normalized phone numbers, tag writes and Pydantic JSON model fields. |
| drf-writable-nested | `/nested-books/` | Create/update an author inside a book payload using the vendor save implementation. |
| drf-flex-fields | `/expanded-books/?expand=author&fields=id,author` | Explicit relation expansion and field selection; the queryset already joins the author. |
| django-polymorphic, django-rest-polymorphic | `/projects/` | Model-specific output and `resourcetype` input discrimination. |
| django-safedelete | `/notes/` | Deletion hides a row without physically removing it. |
| drf-extra-fields, django-cleanup | `/photos/` | Base64 image validation, represented foreign keys and selected-model file cleanup after commit. |
| djangorestframework-dataclasses | `/address/` | Dataclass validation and representation, with no ORM model. |
| nested-multipart-parser | `/multipart/` | Nested form fields and upload metadata, without reading files on the event loop. |
| drf-nested-routers | `/authors/<id>/books/` | Parent-scoped routes and writes that take their parent from the URL. |
| djangorestframework-camel-case | `/camel-case/` | `displayName` in JSON, `display_name` in Python. |
| drf-orjson-renderer | `/orjson/` | Vendor JSON output; it is not the msgspec serializer compiler. |
| drf-excel | `/spreadsheet/` | XLSX export from a normal model serializer. |
| djangorestframework-jsonapi | `/jsonapi/books/` | JSON:API read documents and route-local error formatting. |
| djangorestframework-datatables | `/table/` | DataTables column selection, filtering and pagination metadata. |
| drf-tweaks | `/uncounted/` | Limit/offset pagination without a total-count query. |
| drf-restwind | `/books/` with `Accept: text/html` | Browsable API templates; its app precedes REST framework in template lookup. |
| drf-standardized-errors | Invalid ordinary API requests | Vendor error documents, without replacing aiodrf exception dispatch. |

## Requests

```console
curl -H 'Content-Type: application/json' -d '{"name":"Ursula","phone":"+442079460958"}' http://127.0.0.1:8121/authors/
curl -H 'Content-Type: application/json' -d '{"title":"Earthsea","author":1,"price":"3.50","price_currency":"USD","tags":["sea"],"limits":{"rate":10}}' http://127.0.0.1:8121/books/
curl -H 'Content-Type: application/json' -d '{"title":"Kindred","author":{"name":"Octavia"}}' http://127.0.0.1:8121/nested-books/
curl 'http://127.0.0.1:8121/expanded-books/?expand=author'
curl -H 'Content-Type: application/json' -d '{"resourcetype":"ArtProject","topic":"Painting","artist":"Frida"}' http://127.0.0.1:8121/projects/
curl -F author.name=Ursula -F attachment=@README.md http://127.0.0.1:8121/multipart/
```

## Execution and compatibility

Vendor serializers are synchronous. Generic create/update actions execute their
validation, save and representation in aiodrf's worker; handwritten handlers
use `aio.is_valid`, `aio.save` and `aio.data`. Nothing here claims that Pillow,
the ORM or XLSX generation becomes native asynchronous I/O. The tests exercise
actual ASGI parsing/rendering and persistence.

`drf-writable-nested` owns its `save()` override, which is outside aiodrf's
automatic `ATOMIC_SAVE` boundary. The example demonstrates vendor writes, not
rollback of an entire nested graph. When that invariant is required, wrap the
synchronous `perform_create` / `perform_update` hook in `transaction.atomic()`
and add a late-failure rollback test. This does not turn external file operations
into a database transaction. See [transaction boundaries](../../docs/guides/async-transactions.md).
Soft-deleted
rows need a retention policy; cleanup is deliberately restricted with
`@cleanup.select`. Dynamic vendor fields are not eligible for scalar field
cloning or automatic compilation. Keep DRF's normal path unless the serializer's
documented opt-in contract is suitable.

JSON:API is a read-only example, not an implementation of its entire extension
specification. The compatibility suites cover additional versioning, query and
write contracts; see [the ecosystem guide](../../docs/guides/ecosystem.md).

## Optional RESTQL profile

django-restql is isolated because its pyPEG2 dependency emits compile-time
SyntaxWarning diagnostics on a cold Python 3.12+ import. The default project
does not import that dependency. The optional test records and checks the exact
upstream diagnostic; application code does not suppress warnings or patch pyPEG2.

```console
uv pip install --python .venv/bin/python -r pyproject.toml --group test --extra restql -e ../..
DJANGO_SETTINGS_MODULE=project.settings_restql uv run --no-sync uvicorn project.asgi:application --host 127.0.0.1 --port 8121
DJANGO_SETTINGS_MODULE=project.settings_restql uv run --no-sync pytest tests/test_restql.py -q
curl --get --data-urlencode 'query={title}' http://127.0.0.1:8121/restql/
```

Use this profile to assess an existing RESTQL application's compatibility. It is
not a warning-free dependency recommendation for a new deployment. The ordinary
drf-flex-fields example provides maintained field selection without this parser.
