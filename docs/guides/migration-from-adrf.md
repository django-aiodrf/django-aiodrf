# Migrating from adrf

Compared with adrf 0.1.14. The procedure for a DRF project is in the
[DRF guide](migration-from-drf.md); this one covers what differs for adrf.
The [architectural comparison](../architecture/adrf-comparison.md) explains
the execution and maintenance trade-offs without assuming a migration is needed.

Prefer explicit aiodrf imports and reviewable codemod changes (sections 1 and 2).
The optional compatibility layer (section 0) is an alternative for staged
migration; it installs process-wide import aliases and needs separate review.

## 0. Running adrf code unchanged

```python
# settings.py; uninstall adrf first.
INSTALLED_APPS = [
    "aiodrf",  # before any app whose modules import adrf
    ...,
]
AIODRF = {"ADRF_COMPAT": True}
```

`aiodrf.contrib.adrf_compat` then serves `adrf`, `adrf.views`,
`adrf.viewsets`, `adrf.serializers` and adrf's other modules from aiodrf,
through `sys.modules` aliases: adrf's own code never runs. The layer is off
by default. It is installed when Django loads aiodrf's app, before any
app's models. Code that imports adrf before `django.setup()` calls
`aiodrf.contrib.adrf_compat.install()` first (in `settings.py`). It refuses
to install while adrf is installed, or when adrf was imported already.

Preserved interfaces:

| adrf | Under the layer |
| --- | --- |
| actions `alist`, `acreate`, `aretrieve`, `aupdate`, `partial_aupdate`, `adestroy` and their overrides, `super().alist(...)` included | an override also becomes aiodrf's `list`, `create`, ...; the adrf name on the base class runs aiodrf's action |
| `perform_acreate`, `perform_aupdate`, `perform_adestroy`, `get_apaginated_response` | the same, as `aperform_create`, `aperform_update`, `aperform_destroy`, `aget_paginated_response` |
| `adrf.routers.SimpleRouter`, `DefaultRouter` | route to adrf's action names, so `self.action` is `"alist"` as before; an action the viewset defines under DRF's name (its own `list`) is routed to that name |
| `check_async_permissions(request, permissions)` and the object and throttle variants, overridden | adrf's split: async policies to `check_async_*`, the others to `check_sync_*` in a thread hop; a throttle's `wait()` after an async denial also runs in a thread hop unless declared pure |
| `adrf.permissions.AsyncBasePermission`, `AAND`, `AOR`, `ANOT` | aiodrf's `BasePermission` with async defaults; DRF's operators, which aiodrf evaluates with async operands |
| `adrf.fields.*`, a custom field with `async def ato_representation(self, value)` | DRF's fields; aiodrf awaits that method for fields derived from `adrf.fields` |
| `await serializer.adata`, `acreate`, `aupdate`, `asave`, `async def get_<field>` | aiodrf's serializers |
| `adrf.requests.AsyncRequest`, `adrf.shortcuts`, `adrf.decorators.api_view`, `adrf.test` | aiodrf's request, Django's shortcut, aiodrf's `api_view` and test client |

The shim runs aiodrf implementations: permissions are checked in DRF's order
(section 4), a page is represented in one thread hop (section 6), and
`perform_create` written with DRF's name is called, where adrf would call
only `perform_acreate`. A sample adrf project gives the same responses, with the
same number of thread hops, under the compatibility layer and after conversion
with the codemod.

Each adrf module imported and each adrf method name defined warns once with
`aiodrf.contrib.adrf_compat.AdrfCompatWarning`, a `DeprecationWarning`,
pointing at the import or the class. The layer stays supported; the warning
names aiodrf's equivalent and the codemod. To silence it:

```python
import warnings
from aiodrf.contrib.adrf_compat import AdrfCompatWarning

warnings.filterwarnings("ignore", category=AdrfCompatWarning)
```

## 1. Names

| adrf | aiodrf | Codemod |
| --- | --- | --- |
| `adrf.views`, `generics`, `mixins`, `viewsets`, `serializers`, `decorators`, `permissions`, `routers`, `test` | the `aiodrf` module of the same name | yes |
| `adrf.requests.AsyncRequest` | `aiodrf.request.Request` | yes, imported under its old name |
| `adrf.shortcuts.aget_object_or_404` | `django.shortcuts.aget_object_or_404` | yes |
| `adrf.permissions.AsyncBasePermission`, `AAND`, `AOR`, `ANOT` | `aiodrf.permissions.BasePermission`, `AND`, `OR`, `NOT` (aiodrf evaluates DRF's operators with async operands) | yes, imported under their old names |
| `adrf.generics.aget_object_or_404`, `adrf.mixins.get_data`, adrf's operator helpers (`try_convert_operator`, ...), `adrf.fields`, `adrf.utils` | no counterpart: `await self.aget_object()`, `await aio.data(serializer)`; the helpers are not needed | kept, with a note |
| view actions `alist`, `acreate`, `aretrieve`, `aupdate`, `partial_aupdate`, `adestroy` | DRF's names: `list`, `create`, `retrieve`, `update`, `partial_update`, `destroy`, as `async def` | yes |
| `perform_acreate`, `perform_aupdate`, `perform_adestroy` | `aperform_create`, `aperform_update`, `aperform_destroy` | yes |
| `get_apaginated_response` | `aget_paginated_response` | yes |
| `check_async_permissions`, `check_async_object_permissions`, `check_async_throttles` | `acheck_permissions`, `acheck_object_permissions`, `acheck_throttles` | renamed; the signature is not (below) |
| serializer `acreate`, `aupdate`, `asave` | the same names | left alone; `super().acreate()` / `super().aupdate()` are noted (aiodrf's serializers do not define them) |
| `await serializer.adata` | `await serializer.adata()`; the property spelling still works | no change needed |
| `serializer.is_valid()` | `await serializer.ais_valid()` | no: adrf validates synchronously |

adrf's `check_async_*` methods receive the list of policies to check;
aiodrf's `acheck_*` take `(request)` or `(request, obj)` and select the
policies themselves. An override of one of them has to be rewritten by hand
after the rename.

## 2. The codemod

The codemod needs libcst, installed with the `codemod` extra:

```console
pip install "django-aiodrf[codemod]"
python -m aiodrf.codemod --diff myproject/api/
```

A directory is scanned for `.py` files, skipping hidden directories
(`.venv`, `.nox`, `.git`), `venv`, `site-packages`, `vendor`, `node_modules`,
`build`, `dist`, `__pycache__` and `migrations`, and symbolic links; a file
named on the command line is always converted. A rewritten file keeps its
encoding (PEP 263), line endings and permissions, and is replaced in one step.

Besides the imports it renames the methods of the table inside classes that
are views, when they take `request` first or, as adrf declares them, only
`*args, **kwargs`. `self.<name>` and `super().<name>` references in those
classes follow in every method: a hook only adrf names
(`get_apaginated_response`, `perform_acreate`, ...), a method the class
renamed, and any adrf name when the class derives from adrf's views; any
other reference is left and noted. So are the keyword arguments of
`extend_schema_view()`. A multi-line import keeps its comments and its
trailing comma. String
literals naming an adrf action in an `as_view({...})` method map (the first
argument or `actions=`), such as `as_view({"get": "alist"})`, become DRF's
names; a map built from variables is left alone. A class
is a view when one of its bases, followed through the file's imports if it
was imported under another name, ends in `View` or `ViewSet`. A `Mixin` base
counts only when imported from DRF, adrf or aiodrf, not an application's
`AuditMixin`. Each class body is judged on its own: a serializer nested in a
view keeps its `acreate` and its `super().acreate(...)` call, and a class the
codemod cannot classify is left alone. Base classes imported under another
name are recognized.

Both `from adrf.viewsets import ModelViewSet` and module imports such as
`from adrf import serializers, viewsets as views` are handled. Converted code
behaves as before for CRUD, filtering, policies, custom actions and the OpenAPI
schema, and running the codemod a second time changes nothing. Calls from helper
methods whose signatures cannot be classified need manual review, and so does
every diagnostic note the codemod prints.

Not rewritten, because they are strings: `self.action == "alist"`
comparisons and per-action permission or serializer maps keyed by action
name. Search for the old action names after running it. A routed view that
still defines `alist`, `acreate`, ... with an action's signature (`request`
first, or only `*args, **kwargs`) is reported
by `manage.py check` as `aiodrf.W006` unless the compatibility layer serves
it: aiodrf routes to DRF's names, so such a method is never called.

## 3. Routers

Use DRF's routers (`aiodrf.routers` re-exports them; the codemod moves the
import). adrf's router maps `list` to `alist`, which an aiodrf viewset does
not have. With DRF's action names, `basename`, reverse URL names and
drf-spectacular's list detection are those of a DRF project.

## 4. Policies

adrf gathers all async permissions concurrently and then checks the
synchronous ones. aiodrf asks them in declaration order and stops at the
first denial, as DRF does, looking through `&`, `|` and `~`. When several
policies deny or have side effects, the reported error and the order of
those effects can differ from adrf's.

adrf-style policies (`async def has_permission`) work unchanged, from
synchronous callers too: the browsable API and schema generation no longer
see a coroutine, which is truthy, where they expect a boolean. For new code
subclass
`aiodrf.permissions.BasePermission` and implement `ahas_permission` /
`ahas_object_permission`; likewise `aauthenticate` and `aallow_request`.

## 5. Serializers and writes

Serializer `acreate`, `aupdate` and `asave` keep their names and meaning.
Validation is new: adrf runs DRF's synchronous `is_valid()`, aiodrf has
`ais_valid()` with `async def` validators, `avalidate_<field>` and
`avalidate`, awaited in DRF's order. Code that keeps calling `is_valid()`
from a thread keeps working.

`ATOMIC_SAVE` wraps aiodrf's default synchronous save in a transaction; an
`acreate`/`aupdate` of the project is not wrapped, because async code cannot
run inside a synchronous `atomic` block. Successive awaited writes do not
share a transaction. What must be atomic goes into one synchronous function
with `transaction.atomic()`, awaited once.

## 6. Execution after migration

aiodrf can represent a synchronous page in the worker operation that fetched
it. Async fields and custom hooks can require additional boundaries. Verify
query counts, response contracts and loop behavior with the application's own
dependencies; changing imports alone does not change performance.
