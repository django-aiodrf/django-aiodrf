# Runtime adaptations

By default, aiodrf does not replace Django or DRF methods, modify model base
classes or install import aliases. It works through public extension points,
subclasses, serializer context and its own settings. Registering Django's and
DRF's own classes as safe to run on the event loop, when aiodrf is imported,
records information about them and does not change them.

This page lists every case where aiodrf, or a package it integrates with,
changes something at run time, when that happens and what to keep in mind.

| Mechanism | When it applies | What to know |
| --- | --- | --- |
| Patches of DRF classes | Only the patches named in `AIODRF["MONKEYPATCHES"]`, applied at start-up for the whole process; they replace `ListSerializer.__init__` and add `Response.close` | All patches live in `aiodrf.contrib.monkeypatches`; each has a name and can be reverted with `revert()`. See the [setting](settings.md#monkeypatches). |
| ADRF import aliases | `AIODRF["ADRF_COMPAT"] = True`, before your application's imports; aliases in `sys.modules` for the whole process | ADRF must not be installed at the same time. Restart the process to remove the aliases. Prefer converting your code with the [codemod](../guides/migration-from-adrf.md). |
| Related-key input batching | `FASTDRF["BATCH_RELATED_LOOKUPS"] = True`; replaces `to_internal_value` on eligible field instances of one request, temporarily | No class is changed, and the method is restored in a `finally` block. Custom fields and querysets are not batched. Never share a serializer instance between concurrent requests. |
| Middleware scheduling experiment | Only the `aiodrf.unsafe.middleware` subclasses you install, with their setting enabled | They override their own subclass behaviour, not Django's middleware classes. Configure them before the request handler is created. See [middleware scheduling](../guides/unsafe-middleware.md). |
| django-valkey's cache-close receiver | Importing django-valkey's backend replaces Django's `request_finished` cache-close receiver in the process | This is django-valkey's behaviour (tested with 0.4.1). Restart the process to remove it. aiodrf's `LifespanConnectionFactory` uses a public hook and changes neither the receiver nor the cache registry. See [native async cache](../guides/async-cache.md). |
| django-async-backend's model managers | Installing `aiodrf.contrib.async_backend` with django-async-backend | django-async-backend adds its native manager and API to Django models. aiodrf's default installation does not. See the [native PostgreSQL backend](../guides/async-backend.md). |
| django-tasks typing support | Importing the `django-tasks` backport | The backport calls `django_stubs_ext.monkeypatch()` itself. aiodrf imports it only through the `tasks` extra. See [Django Tasks](../guides/tasks.md). |
| Other packages | Packages you install, such as django-cachalot, django-cacheops or tracing libraries | These can instrument Django or their own dependencies; aiodrf neither prevents nor hides it. |

The field cache's copy plans do **not** replace `Field.__deepcopy__`,
`Field.__init__` or DRF's serializer classes. They are private plans kept per
serializer class, with a separate memo dictionary for each copy. Like DRF's
constructors, the scalar copy path increments DRF's field creation counter.

See also [limitations](limitations.md) and the
[upstream internals](upstream-internals.md) aiodrf relies on.
