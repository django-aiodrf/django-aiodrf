# Package and API naming

The distribution name is `django-aiodrf`. Python
imports and the Django application label remain `aiodrf`. These are distinct
names: install `django-aiodrf`, then import `aiodrf` and add `"aiodrf"` to
`INSTALLED_APPS`.

## Asynchronous operations

HTTP handlers keep Django's names: `async def get()`, `post()` or `query()`.
Viewset actions keep DRF's `list`, `retrieve`, `create`, `update` and `destroy`
names. They are coroutine handlers, not a second set of `alist` actions.

Hooks with a synchronous counterpart use an `a` prefix: `aget_queryset`,
`aget_serializer`, `ahas_permission`, `aauthenticate_header`, `ais_valid` and
`asave`. `await serializer.adata()` is the primary representation API;
`await serializer.adata` remains a migration-compatible spelling.

Unmodified DRF instances can be used through `aiodrf.aio` and the generic views.
The [extension guide](../guides/extension-hooks.md) explains override selection.
