# Django fetch modes

Django 6.1 adds `QuerySet.fetch_mode()` to control reads of unloaded foreign keys,
one-to-one relations, deferred columns and generic relations. `FETCH_ONE` is
the default. `FETCH_PEERS` batches missing values for instances from the same
queryset; `FETCH_RAISE` raises `FieldFetchBlocked` instead of reading implicitly.
Related-manager queries are not themselves batched by this mechanism. See
[Django's reference](https://docs.djangoproject.com/en/6.1/topics/db/fetch-modes/).

## Configuration

```python
AIODRF = {
    "FETCH_MODE": "raise",  # None (unchanged), "peers", or "raise"
}
```

Generic views apply this mode on Django versions that provide it. On older
supported versions the setting cannot enforce fetch modes; it is not a backport.
For endpoint-specific selection, keep the global setting at `None` and use
Django's public API:

```python
from django.db import models

queryset = Book.objects.select_related("author").fetch_mode(models.FETCH_RAISE)
```

An explicit global mode can override a mode already selected on the queryset.
Choose one place for the application's policy.

## Execution boundary

Fetch modes do not change the database driver. Missing-field access can still
perform synchronous I/O. `FETCH_PEERS` does not make inline representation safe:
load known relations explicitly and keep lazy representation in a worker.
Use `FETCH_RAISE` in tests to expose unintended reads.

`Meta.auto_prefetch` is a separate optional optimizer under
`aiodrf.contrib.builtin.prefetch`. It derives explicit loading paths from serializer
fields; fetch modes govern later implicit access. Neither replaces authorization.
