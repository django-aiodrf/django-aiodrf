# Maintaining compatibility with Django and DRF

aiodrf repeats some of DRF's code in its async paths and reads a few names that
Django, DRF and asgiref do not document as public. This guide is for
maintainers updating aiodrf for a new release of those packages. It lists the
code that follows upstream, what to look at in each place, and the check that
catches a change.

Review every DRF release, patch releases included: DRF 3.18.0 changed four of
the functions aiodrf follows. Review Django's feature releases; its patch
releases rarely touch what aiodrf reads. Review every asgiref release, and the
optional packages listed under [optional packages](#optional-packages) when
their adapters are affected.

Two test modules record the dependencies on upstream code:

- `tests/test_upstream_mirrors.py` keeps, for each DRF function aiodrf follows
  step by step, an AST digest per supported version (docstrings excluded) and
  the aiodrf code that follows it.
- `tests/test_upstream_internals.py` checks that every private upstream name
  aiodrf reads still exists. [Upstream internals](docs/reference/upstream-internals.md)
  explains what happens without each one.

This guide covers those modules and the code around them that no test tracks.

## Order of work

1. Run `nox -s canary`, the test suite against the development branches of
   Django and DRF, before the release is published. A failure points to one
   of the recorded dependencies or to a change of behaviour.
2. Install the new version and run the recorded dependencies:

   ```console
   pytest tests/test_upstream_mirrors.py tests/test_upstream_internals.py tests/async_backend/test_mirrors.py
   ```

   - An unknown digest: read the changed DRF function, update the aiodrf code
     the test names (or confirm that nothing needs to change), and record the
     digest for the new version.
   - A missing private name: the matching row of
     [upstream internals](docs/reference/upstream-internals.md) describes the
     consequence.
3. Run `nox -s drf_parity` for the new DRF: DRF's own test suite with aiodrf's
   views in place of DRF's (`tests/drf_parity/aiodrf_drf_parity.py`). The
   session downloads the release of the installed version and installs its
   extra test requirements (`dj-database-url`, and `pytz` for 3.16 and 3.17);
   a new release may need another.
4. Run `nox -s differential` and the `tests` session for the new version pair.
5. Update the [version registry](#adding-or-dropping-a-version), then the
   documentation.
6. Fix deprecation warnings. The suite turns warnings into errors
   (`filterwarnings = ["error"]` in `pyproject.toml`), so a new DRF or Django
   deprecation fails tests. Search `src/aiodrf` for the deprecated name; a
   version-dependent fix belongs in `compat.py`.

## DRF code that aiodrf follows

`tests/test_upstream_mirrors.py` names, for each changed DRF function, the
aiodrf code to update. The table groups the same functions by aiodrf module,
so that a change in one area can be reviewed as a whole.

| aiodrf module | Follows | What to look for |
| --- | --- | --- |
| `src/aiodrf/views.py`: `APIView.dispatch`, `ainitial`, `_RequestPlan.initial`, `_RequestPlan.initialize_request`, `_finalize_response`, `ahandle_exception`, `acheck_permissions`, `acheck_object_permissions`, `acheck_throttles` | `APIView.dispatch`, `initial`, `initialize_request`, `finalize_response`, `handle_exception`, `check_permissions`, `check_object_permissions`, `check_throttles` | New steps in `initial` or `dispatch`; header handling in `finalize_response` (3.18 added a helper that splits `Vary`); the `auth_header` and 403 rule of `handle_exception`; `default_response_headers`, which `_RequestPlan.headers` reproduces (`Allow`, `Vary`) |
| `src/aiodrf/views.py`: content negotiation (`_perform_content_negotiation`, `_RequestPlan.negotiate`; `_accept_header` is django-fastdrf's) | `DefaultContentNegotiation.select_renderer`, `filter_renderers`, `get_accept_list` | The three functions are pinned in `_DRF_NEGOTIATION`; if one changes, the negotiation cache is turned off without an error. 3.18 moved `get_accept_list` from `META` to `request.headers` (`fastdrf.views._ACCEPT_FROM_HEADERS`); `URL_FORMAT_OVERRIDE` |
| `src/aiodrf/request.py`: `_authenticate`, `_aauthenticate`, `adata`, `_parses_inline`, the `user` setter | `Request._authenticate`, `_load_data_and_files`, `_parse`, the `user` and `auth` setters | The `_not_authenticated` contract, `ForcedAuthentication`, parser selection |
| `src/aiodrf/serializers.py`: `many_init`, the synchronous bridges, `_check_no_coroutines`, `_CONVERTING_FIELDS`, `ListSerializer.to_representation` | `BaseSerializer.many_init`, `is_valid`, `save`, `Serializer.to_representation`, `ListSerializer.to_representation`; `LIST_SERIALIZER_KWARGS`, `LIST_SERIALIZER_KWARGS_REMOVE` | New list keyword arguments; a field class whose `to_representation` stops converting its value (remove it from `_CONVERTING_FIELDS`) |
| `src/aiodrf/aio/_validate.py`: `default_is_valid`, `default_run_validation`, `_ato_internal_value`, `_convert_value`, `_arun_field`, `_arun_field_validators`, `_arun_validators`, `_alist_to_internal_value`, `_list_errors` | `BaseSerializer.is_valid`, `Serializer.run_validation`, `to_internal_value`, `run_validators`, `_read_only_defaults`, `ListSerializer.to_internal_value`, `run_child_validation`, `Field.run_validation`, `run_validators`, `validate_empty_values`, `CharField.run_validation`, `RelatedField.run_validation`, `ListField.to_internal_value`, `DictField.to_internal_value` | Error formats (3.18 added list errors as a dictionary and `RemovedInDRF320Warning`); the fields that override `run_validation` (`_convert_value` handles the three that do, and a fourth needs its own branch); the rules for empty, blank and `None` values |
| `src/aiodrf/aio/_save.py`: `default_save`, `_acall_write`, `_write_aliases` | `BaseSerializer.save`, `ListSerializer.save`, `ListSerializer.create` and their assertions | New checks in `save`; the semantics of `ListSerializer.update` |
| `src/aiodrf/aio/_represent.py`: `default_data`, `default_to_representation`, `_represent_fields`, `_afield_representation`, `_represent` | `BaseSerializer.data`, `Serializer.to_representation` (the `SkipField`, `PKOnlyObject` and `None` rules) | Any change in the loop over fields |
| `src/aiodrf/aio/_classify.py`, `src/aiodrf/aio/_loaded.py`: `_BUILTIN_VALIDATORS`, `_VALUE_FIELDS`, `_DESCRIPTORS`, `_plan` | The classes of `rest_framework.validators`; `Field.get_attribute`, `PrimaryKeyRelatedField.use_pk_only_optimization` | A new field class, to be added where it belongs: `_VALUE_FIELDS` if representing a loaded value does no I/O, `_CONVERTING_FIELDS`. django-fastdrf's tables (`_BUILTIN_FIELDS`, `_FIELD_HOOKS`, `_BUILD_HOOKS`, `compiler._SCALARS`, `inputs.py`, `_field_copy.py`) are changed and released there first. `BigIntegerField` (DRF 3.17) is read through `compat.BigIntegerField` in three places (and `fastdrf.compat`) |
| `src/aiodrf/mixins.py`: `_create`, `_list`, `_list_page`, `_retrieve`, `_update`, `_destroy` | The five model mixins | The `perform_*` hooks, `get_success_headers`, the reset of the prefetch cache in `update` |
| `src/aiodrf/generics.py`: `_object`, `aget_object`, `optimize_queryset`, `get_serializer`, `filter_queryset`, `paginate_queryset` | `GenericAPIView.get_object`, `get_serializer`, `get_serializer_context`, `filter_queryset`, `paginate_queryset`, `get_paginated_response` | The `lookup_url_kwarg` assertion; the permission check in `get_object` |
| `src/aiodrf/policies.py`, `authentication.py`, `throttling.py`, `_builtins.py` | `AND`, `OR`, `NOT` (`ahas_object_permission` repeats DRF's second `has_permission` check in `OR`); `SessionAuthentication.authenticate` and `enforce_csrf`; `SimpleRateThrottle.allow_request`, `wait`, `get_cache_key`; the DRF classes registered as safe on the event loop in `_builtins.py` | A DRF policy class that starts doing I/O or calling a new method (its registration is then wrong); new members of `SimpleRateThrottle` (add them to `_RATE_THROTTLE_HOOKS`) |
| `src/aiodrf/response.py`: `_PLAIN`, `_render_checked` | The types `encoders.JSONEncoder` converts | A new type the encoder converts (`_PLAIN`). The kept-encoder renderer (`fastdrf.renderers`, `_dumps_encoder`) is django-fastdrf's |
| `src/aiodrf/contrib/permissions.py` | `DjangoModelPermissions.has_permission`, `_queryset`, `get_required_permissions` | The `perms_map` entry for `GET` (requiring the `view` permission upstream would change it) |
| `src/aiodrf/test.py` | `APIRequestFactory._encode_data`, `APIClient.request`, `logout`, `force_authenticate`, `credentials` | `TEST_REQUEST_RENDERER_CLASSES`; the signature of `_encode_data` |
| `src/aiodrf/decorators.py` | `rest_framework.decorators` (the decorators added in 3.17 are imported in a `try`/`except ImportError`) | A new decorator, to be imported and added to `__all__` |
| `src/aiodrf/contrib/adrf_compat/` | adrf's API on DRF's classes (`adrf/fields.py` also handles `BigIntegerField`) | adrf no longer changes; DRF changes reach it through the modules above |
| `src/aiodrf/contrib/async_backend/` | DRF, Django and django-filter code reimplemented on the native ORM, recorded in `tests/async_backend/test_mirrors.py`; pinned to `django-async-backend~=6.1.5`, Django 6.1 and DRF 3.18 | Run `nox -s native_db`. The package copies Django 6.1's ORM, so Django 6.2 needs a new release of it first |

The following code follows Django or asgiref and is not recorded yet. The same
digest mechanism works for any importable function, so these are candidates
for `tests/test_upstream_mirrors.py`:

| aiodrf | Follows | Risk |
| --- | --- | --- |
| `src/aiodrf/asgi.py`: `_ThreadKeepingHandler.__call__` | `django.core.handlers.asgi.ASGIHandler.__call__` (the `scope["type"]` check and the `ThreadSensitiveContext` wrapper) | A new step in Django's `__call__` is skipped by applications that use `REQUEST_THREADS` |
| `src/aiodrf/asgi.py`: `_LentThreadContext.__aenter__`, `__aexit__` | `asgiref.sync.ThreadSensitiveContext` | A change to asgiref's executor map (`context_to_thread_executor`) or its token protocol |
| `src/aiodrf/cache.py`: `cache_page` | `django.views.decorators.cache.cache_page` and the call order of `CacheMiddleware.process_request` and `process_response` | New middleware steps |
| `src/aiodrf/contrib/async_cache.py`: `_Policy`, `_PolicyCache` | The reads and writes of `CacheMiddleware` (`get_cache_key`, `learn_cache_key`) | A new read is fetched on the event loop; a change in the order of writes matters |
| `src/aiodrf/views.py`: `_acall_conditionally`, `_avalidators` | `django.views.decorators.http.condition` (`get_conditional_response`, ETag quoting, `Last-Modified`) | Changes to Django's RFC 9110 evaluation |
| `src/aiodrf/contrib/_cache_backend.py` | The semantics of `django.core.cache.backends.redis.RedisCache` (`get_backend_timeout`, `incr`, `touch`, `RedisSerializer`) | A changed `BaseCache` default (`aget_or_set`, `_missing_key`) |
| `src/aiodrf/management/__init__.py`: `AsyncCommand.execute`, `handle` | `BaseCommand.execute` (where `handle` is called, output handling) | Django adding native async commands ([#31793](https://code.djangoproject.com/ticket/31793)), which would reduce this module to a compatibility layer |
| `src/aiodrf/test.py`: `AsyncAPIClient`, `AsyncAPIRequestFactory` | `django.test.client.AsyncClient`, `AsyncRequestFactory` (header translation in `generic`, keyword arguments forwarded by `_ahandle_redirects`) | Changed arguments break requests with `follow=True` |

## Private upstream names

`tests/test_upstream_internals.py` checks the names, and
[upstream internals](docs/reference/upstream-internals.md) lists them with the
consequence of each one disappearing. They belong to Django, DRF, asgiref,
drf-spectacular (the code constants of `drainage.isolate_view_method`, read by
`utils._is_transparent_wrapper`) and django-mongodb-backend
(`features._supports_transactions`).

Names read with a default need a closer look even when the test passes: a
rename does not fail, it changes where code runs.

- `SimpleLazyObject._setupfunc` (`authentication._lazy_setup_module`): every
  lazy user would be evaluated in a worker thread. Slower, but correct.
- `Model._prefetched_objects_cache` (`mixins._update`, `aio._loaded`):
  prefetched relations would be represented in a worker thread and not reset
  after an update, unlike DRF, which returns a stale nested list after `PUT`.
- `view._ignore_model_permissions` (`contrib.permissions`): the router's root
  view would be checked for model permissions.
- drf-spectacular's wrapper constants: methods wrapped by
  `extend_schema_view` would run their first steps in a worker thread.

## Adding or dropping a version

Adding or dropping a Django or DRF version touches the following places:

| Where | What |
| --- | --- |
| `pyproject.toml` | The minimum versions in `dependencies` (`django>=5.2`, `djangorestframework>=3.16`, `asgiref>=3.8.1`, `django-fastdrf>=0.4,<0.5`), the `Framework :: Django :: X.Y` classifiers, the pin of the `async-backend` extra |
| `noxfile.py` | `MATRIX` (the version pairs of `tests` and `unit`), the parameters of `drf_parity` and its per-version test requirements, the pairs of `adrf_compat`, `differential` and `ecosystem_mongodb`, the exact minimum versions of `tests_minimum`, and `FASTDRF`, the django-fastdrf requirement every session installs (`AIODRF_FASTDRF` overrides it) |
| `.github/workflows/tests.yml` | The version pairs of the `matrix` and `unit` jobs; the `drf_parity`, `adrf_compat` and `differential` entries of the `compatibility` job |
| `src/aiodrf/compat.py` | Each flag states the version that ends it (see [raising a minimum version](#raising-a-minimum-version)). Raising a minimum version is a search for the flag in `views.py`, `checks.py`, `generics.py`, `cache.py`, `serializers.py`, `aio/_validate.py`, `aio/_loaded.py`, `decorators.py` and `contrib/adrf_compat/` |
| `src/aiodrf/settings.py` | `ASGIREF_VERSION` (`UNSAFE_SYNC_MIDDLEWARE` needs asgiref 3.12.1 or later); the reference-counting tests of closed responses need asgiref 3.9 or later |
| `tests/test_upstream_mirrors.py`, `tests/async_backend/test_mirrors.py` | One digest per version: add the new version's, remove the dropped one's |
| Version-dependent skips in `tests/` | `skipif(DRF_VERSION < ...)`, `skipif(FETCH_RAISE is None ...)`, `importorskip("django.tasks")`: remove them with the minimum version |
| `docs/guides/compatibility.md`, `docs/guides/releasing.md`, `docs/reference/ecosystem-versions.md`, `README.md`, `CHANGELOG.md` | The supported versions and the "Compatibility" entry of the changelog |

`tests/test_python_compatibility.py` and
`tests/test_dependency_configuration.py` check part of this agreement. Confirm
that they cover a row before relying on them.

## Settings and classes aiodrf lists explicitly

A DRF release that adds a setting or a class does not make a test fail by
itself. Compare these lists with the release notes:

- `REST_FRAMEWORK` keys read directly: `URL_FORMAT_OVERRIDE` and
  `FORMAT_SUFFIX_KWARG` (views), `LIST_SERIALIZER_ERRORS_AS_DICT`
  (`aio/_validate.py`), `NON_FIELD_ERRORS_KEY`, `TEST_REQUEST_*` (`test.py`)
  and `UNAUTHENTICATED_USER` (through `_not_authenticated`). django-fastdrf
  reads `COERCE_BIGINT_TO_STRING`, `DATETIME_FORMAT`, `DATE_FORMAT` and
  `TIME_FORMAT` (`fastdrf/compiler.py`) and `LIST_SERIALIZER_ERRORS_AS_DICT`
  (`fastdrf/typed.py`); a new key that changes output or validation is
  handled there (`clear_compiled`, `analyze`).
- Field tables maintained by hand in aiodrf: `serializers._CONVERTING_FIELDS`,
  `aio/_loaded._VALUE_FIELDS` and `contrib/adrf_compat/adrf/fields.py`.
  django-fastdrf keeps its own (`_classify._BUILTIN_FIELDS`, built from DRF's
  modules; `_SCALARS`, `_INTEGERS`, `_STRINGS` and `_STRICT_DECLINES` in
  `compiler.py`, the recognizers in `inputs.py`, `_field_copy.py`,
  `convert.py`), with its own upstream ledger (`tests/test_upstream_mirrors.py`).
- Validators: `aio/_classify._BUILTIN_VALIDATORS` (Django's validators module
  and `ProhibitSurrogateCharactersValidator`), and the `UniqueValidator`
  family, which is always treated as doing I/O.
- Classes registered as safe on the event loop in `_builtins.py`: `AllowAny`,
  `IsAuthenticated`, `IsAdminUser`, `IsAuthenticatedOrReadOnly`,
  `ForcedAuthentication`, `JSONRenderer`, `JSONParser`, `FormParser`,
  `DefaultContentNegotiation`, the five versioning classes,
  `get_paginated_response` of the three paginators, and the throttles'
  `wait`. Read the diff of each class DRF changes: a new database query or a
  new overridable call makes its registration wrong. `SearchFilter` and
  `OrderingFilter` are not registered for that reason.
- `policies._RATE_THROTTLE_HOOKS` lists every method that
  `SimpleRateThrottle.allow_request` calls.
- `_PLANNED_HOOKS` and `_PLANNED_ATTRIBUTES` in `views.py` list every `APIView`
  method that `dispatch` and `initial` call, and every class attribute the
  request plan records. A new `APIView` hook missing from the first list would
  not be seen when a project overrides it.

## Django and asgiref

- `django.core.handlers.asgi.ASGIHandler` (`asgi.py`): `__call__`, `handle`,
  request body spooling (`FILE_UPLOAD_MAX_MEMORY_SIZE`, read by
  `request._parses_inline`) and when `response.close()` is called, which
  triggers `_release`.
- `django.views.generic.View`: `http_method_names` (when Django adds `QUERY`,
  [#37232](https://code.djangoproject.com/ticket/37232), remove the
  `DJANGO_HAS_QUERY` branches in `views.py`), the `head` alias set in `setup`
  (`response._release` deletes it by name) and `view_is_async`.
- `django.template.response.SimpleTemplateResponse`: `render`, `_is_rendered`
  and `_post_render_callbacks` (`response._Render`).
- Fetch modes: `FETCH_PEERS`, `FETCH_RAISE` and `QuerySet.fetch_mode`
  (`generics.optimize_queryset`, `contrib/async_backend/views.py`; the system
  check is django-fastdrf's `fastdrf.E006`).
- Related descriptors and caches: `_state.fields_cache`,
  `_prefetched_objects_cache`, `ForwardManyToOneDescriptor`,
  `ReverseManyToOneDescriptor`, `ManyToManyDescriptor` and `DeferredAttribute`
  (`aio/_loaded._DESCRIPTORS`; django-fastdrf's
  `compiler._check_framework_read` and `_related_manager_model`). A new Django descriptor class is treated as
  unknown until it is added to these tables.
- `django.core.cache`: `ConnectionProxy`, the registrations of `LocMemCache`
  and `DummyCache`, and the async defaults of `BaseCache`
  (`contrib/_cache_backend.py`).
- `django.test.client`: `AsyncClient`, `AsyncRequestFactory` and
  `AsyncClientHandler` (`test.py`).
- The lazy user of `django.contrib.auth.middleware`: `asession_authenticate`
  recognizes it by the module of `_setupfunc`, and django-structlog's wrapped
  user in the same way.
- `django.utils.cache.get_conditional_response` (conditional requests).
- Signals: `request_finished` receivers run from `response.close()`, and
  aiodrf's `_release` runs after Django's `close`. The order matters if Django
  changes what `close` does.
- asgiref: the executor selection of `SyncToAsync`, `ThreadSensitiveContext`,
  `markcoroutinefunction` and `iscoroutinefunction` (`utils.awaits_inline`
  reads the `CO_COROUTINE` flag and falls back to asgiref's marker),
  `async_to_sync` in the bridges, and `Local` (the teardown of in-memory
  SQLite connections in `conftest.py`).
- Async features added to Django can make aiodrf code redundant: async
  management commands ([#31793](https://code.djangoproject.com/ticket/31793)),
  `QUERY` in `View`, `aauthenticate` in `AuthenticationMiddleware`, async
  cache methods (already used) and the async paginator (aiodrf uses one thread
  switch instead of `AsyncPaginator`'s two, as explained in `pagination.py`).

## django-fastdrf

aiodrf depends on django-fastdrf for the serializer optimizations: the
compiler, input recognition, field caching and copying, related lookups,
auto-prefetch, the msgspec renderer and parser, `DataResponse` and the
schema-serializer core. A change to that code is made in django-fastdrf,
tested and released there first; aiodrf then raises its requirement
(`pyproject.toml`, `FASTDRF` in `noxfile.py`). To test aiodrf against a
django-fastdrf checkout, set `AIODRF_FASTDRF` to its path. django-fastdrf
keeps its own ledger of the DRF functions its code repeats
(`tests/test_upstream_mirrors.py` there); aiodrf's ledger lists only aiodrf's
counterparts.

## Optional packages

| Package | Adapter | Supported versions | What to check |
| --- | --- | --- | --- |
| drf-spectacular | `contrib/spectacular/`, `utils._is_transparent_wrapper` | `>=0.28` | The code constants of `isolate_view_method` in `extend_schema_view`; the nullable forms of OpenAPI 3.0 and 3.1 in `extensions._openapi_30` |
| django-filter | `contrib/django_filters.py`, `contrib/async_backend/filters.py` | `>=25.1` | The native filters reimplement how a `FilterSet` is evaluated |
| msgspec | `contrib/msgspec/` (subclasses of django-fastdrf's), `contrib/cache_codecs.py`; django-fastdrf: `fastdrf/msgspec/`, `compiler.py`, `inputs.py`, `convert.py` | `>=0.19` | `Struct` options (`rename`, `kw_only`), the strictness of `convert`, `Meta` constraints (`convert.py`) |
| pydantic | `contrib/pydantic/` (subclasses of django-fastdrf's); django-fastdrf: `fastdrf/pydantic/`, `inputs.py`, `codecs.PydanticCodec`, `typed.serializer_kind` (which refuses version 1) | `>=2.9` | `__pydantic_serializer__`, `TypeAdapter` configuration errors (`type-adapter-config-unused`), `Field` metadata |
| django-async-backend | `contrib/async_backend/` | `~=6.1.5` | It copies Django's ORM, so each Django release needs a new release of it; `tests/async_backend/test_mirrors.py` |
| django-mongodb-backend | `contrib/mongodb/` | Tested in `ecosystem_mongodb` | `features._supports_transactions` |
| redis, valkey, django-valkey | `contrib/redis.py`, `contrib/valkey.py`, `contrib/_cache_backend.py` | `>=5.0.1`, `>=0.4.1` | `from_url`, `BlockingConnectionPool`, the Sentinel and Cluster constructors, the retry classes |
| django-opensearch-dsl, opensearch-py | `contrib/opensearch.py` | `>=0.8`, `>=3.2` | Tested in `ecosystem_opensearch` |
| whitenoise | `contrib/whitenoise.py` | `>=6.12` | The `WhiteNoiseMiddleware(get_response)` contract |
| opentelemetry-api | `contrib/opentelemetry.py` | `>=1.27` | How `ProxyTracerProvider` is detected |
| adrf (not a dependency) | `contrib/adrf_compat/`, `codemod/` | adrf no longer changes | The codemod's renaming tables |
| django-structlog, django-rest-knox, djangorestframework-simplejwt, drf-auth-kit | `authentication._PASS_THROUGH_USERS`, `contrib/knox`, `contrib/simplejwt`, `contrib/auth_kit` | Tested in `ecosystem` | Each registers a credentials check that repeats the first steps of the package's `authenticate()` |

[Ecosystem versions](docs/reference/ecosystem-versions.md) states the tested
versions. `requirements/ecosystem/requirements.txt` pins them for the nox
sessions and is kept up to date by Dependabot.

## Raising a minimum version

Code that becomes unnecessary when a minimum version is raised:

- DRF 3.17: `compat.BigIntegerField` and the three places that read it; the
  minimum versions of `tests_minimum`.
- DRF 3.18: `DRF_HAS_LIST_ERRORS_AS_DICT`, the
  list-format branch of `aio/_validate._list_errors`, and the
  `try`/`except` around the 3.17 decorators in `decorators.py`.
- Django 6.1: the `FETCH_PEERS`/`FETCH_RAISE` import fallback in `compat.py`
  (and the `None` entries of `generics._FETCH_MODES`) and the tests skipped
  when `FETCH_PEERS` is `None`; the code for the `django-tasks`
  backport (`tests/test_tasks.py`, `examples/tasks-django5`).
- A Django version that dispatches `QUERY`: `DJANGO_HAS_QUERY`, its two
  branches in `views.py` and the comment in `AsyncAPIRequestFactory.query`.
- asgiref 3.12.1: the version check of `UNSAFE_SYNC_MIDDLEWARE`.

## Before releasing an update

Run `nox -s tests lint typecheck docs drf_parity differential adrf_compat`,
the service sessions the change affects (`tests_postgres`, `native_db`,
`ecosystem*`) and `examples`, which resolves the new version because the
examples do not pin it. Add the "Compatibility" entry to `CHANGELOG.md`, and
update the recorded digests in the same change as the code they cover. The
release itself follows [RELEASE.md](RELEASE.md).
