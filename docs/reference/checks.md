# System checks

aiodrf registers its checks with Django's system check framework: `python
manage.py check` runs them, `--deploy` adds the deployment ones, and
`SILENCED_SYSTEM_CHECKS` silences an id.

```console
python manage.py check --deploy
python manage.py check --deploy --tag compatibility
```

aiodrf also registers django-fastdrf's settings check (`fastdrf.E001` to
`fastdrf.E004` and `fastdrf.E006`), which reports the `FASTDRF` settings: a value outside its
choices, an unknown key, a `SERIALIZER_BACKEND` whose package is not
installed (`fastdrf.E004`), and a `FETCH_MODE` on a Django without fetch modes
(`fastdrf.E006`), and its hint `fastdrf.I001`: a compiling
`SERIALIZER_BACKEND` with django-phonenumber-field, django-countries or
django-money installed without its `fastdrf.contrib` application.
`fastdrf.E005` runs only with `"fastdrf"` in
`INSTALLED_APPS`, for django-fastdrf's own views; aiodrf's views are checked by
`aiodrf.E005`. See django-fastdrf's documentation for the list.

## Errors

| ID | Condition | Action |
| --- | --- | --- |
| `aiodrf.E001` | A setting whose values are a fixed set (`REPRESENTATION_MODE`, `VALIDATION_UNKNOWN`, ...) has another value | Use one of the values the [settings reference](settings.md) lists. |
| `aiodrf.E002` | An import string (`INLINE_RENDERERS`, `PURE_POLICIES`, `LIFESPAN`, ...) cannot be imported | Correct the dotted path, or install the package. |
| `aiodrf.E003` | `AIODRF` is not a mapping | Make it a `dict` of settings. |
| `aiodrf.E005` | A view's serializer or schemas are of a kind `ALLOWED_SERIALIZER_BACKENDS` excludes | Change the view or the setting; `as_view` refuses the same view. |
| `aiodrf.E006` | A setting's value has the wrong type or form (a list that is not a list of strings, `ATOMIC_SAVE` that is not a `bool`, a `LIFESPAN` that is not an async context manager factory, ...) | Correct the value; the message names the setting and what it expects. |
| `aiodrf.E007` | An import string of `PURE_POLICIES` or another list of classes names something that is not a class | Name the class. |

## Warnings

| ID | Condition | Action |
| --- | --- | --- |
| `aiodrf.W001` | `DJANGO_ALLOW_ASYNC_UNSAFE` outside `DEBUG` | Remove it; retain Django's safety guards. |
| `aiodrf.W002` | `ATOMIC_REQUESTS` | Choose explicit synchronous transaction units; an async request is not one transaction. |
| `aiodrf.W003` | Unknown `AIODRF` key | Correct the setting rather than relying on a silent default. |
| `aiodrf.W004` | ASGI configured and a database has nonzero `CONN_MAX_AGE` (`--deploy`) | Use `CONN_MAX_AGE=0`; consider the backend's connection pool. |
| `aiodrf.W005` | ASGI configured and middleware lacks `async_capable` (`--deploy`) | Inspect adaptation and long-lived request concurrency; replace only if needed. |
| `aiodrf.W006` | A routed view defines adrf's action names (`alist`, `acreate`, ... taking `request`) without the adrf compatibility layer | Rename them with `python -m aiodrf.codemod`; aiodrf routes to DRF's names, so they are never called. |
| `aiodrf.W007` | A MongoDB database (django-mongodb-backend) with `ATOMIC_SAVE` on and without `aiodrf.contrib.mongodb` | Add the contrib: the backend makes `transaction.atomic()` a no-op, so a failed save is not rolled back ([guide](../guides/async-nosql.md#transactions)). |
| `aiodrf.W008` | A DRF (not aiodrf) view in the URLconf uses a permission, authentication or throttle class on DRF's base class that defines only the async member (`ahas_permission`, `ahas_object_permission`, `aauthenticate`, `aallow_request`) | Define the sync member too, or subclass aiodrf's base class: DRF calls DRF's default, which allows every request (permissions) or raises `NotImplementedError` ([guide](../guides/drf-integration.md#3-policies-and-shared-code)). |
| `aiodrf.W010` | A DRF (not aiodrf) view in the URLconf uses a DRF serializer (its `serializer_class`, or a nested serializer of it) with async members: `async def validate_<field>`, `validate`, `create`, `update`, `get_<field>` of a method field, or aiodrf's `a...` names | Inherit from `aiodrf.serializers`, or serve the view with aiodrf's views: DRF's `is_valid()`, `save()` and `.data` call the members without awaiting, so a coroutine reaches `validated_data`, the saved row or the response, and DRF never calls the `a...` names ([guide](../guides/drf-integration.md#2-serializers-across-the-two-kinds)). |

W004 and W005 run only with `--deploy` and a truthy `ASGI_APPLICATION`. They
inspect configuration, import middleware factories but do not construct them,
and open no connections. Imported application modules can have their own side
effects. These checks cannot establish whether the actual server uses ASGI,
what a proxy buffers, or whether an application-labelled async callable blocks
internally.
