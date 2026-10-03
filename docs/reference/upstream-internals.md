# Upstream internals aiodrf reads

aiodrf patches nothing by default ([runtime adaptations](runtime-adaptations.md)
lists what it replaces when asked). It does read names that Django, DRF,
asgiref and two optional packages do not promise. Each is listed here with
what aiodrf does without it. aiodrf's tests check that each name exists, so a
release that renames one is detected before aiodrf supports it.

The DRF functions whose steps aiodrf repeats are a separate ledger,
kept in aiodrf's tests: a change to any of them in a new DRF release is
detected, together with the aiodrf function to update.

| Owner | Name | Read by | Without it |
| --- | --- | --- | --- |
| Django | `QuerySet._result_cache` | `aio._represent`, `aio._save` | `AttributeError` when a queryset is represented or a list saved. |
| Django | `Model._prefetched_objects_cache` | `mixins`, `aio._loaded` | Read with a default: prefetched relations are not seen as loaded (represented in a worker) and not cleared after an update, which DRF's `update` does. |
| Django | `Model._state.fields_cache` | `aio._loaded` | `AttributeError` when aiodrf checks whether a representation reads loaded values only. |
| Django | `SimpleLazyObject._setupfunc` | `authentication` | Django's lazy user is not recognised: the user is resolved in a worker. |
| Django | `ConnectionProxy._connections`, `_alias` | `compat.resolve_cache` | `AttributeError` when a cache proxy is resolved (throttles, policies). |
| Django | `SimpleTemplateResponse._is_rendered`, `_post_render_callbacks` | `response` | `AttributeError` when a response is finalized. |
| Django | `HttpResponse._reason_phrase` | `fastdrf.response.DataResponse` | `AttributeError` when a `DataResponse` becomes DRF's `Response`. |
| Django | `StreamingHttpResponse._iterator` | `response` (streaming) | A stream's iterator is not closed on disconnect. |
| Django | `AsyncClient._ahandle_redirects`, `AsyncRequestFactory._base_scope` | `test` | `AttributeError` in the test client (`follow=True`, every request). |
| Django | `HttpRequest._dont_enforce_csrf_checks` | `test` | The test client's `enforce_csrf_checks=False` is ignored by Django's CSRF middleware. |
| DRF | `Request._request`, `_full_data`, `_load_data_and_files`, `_not_authenticated`, `request._hasattr`, `request.wrap_attributeerrors` | `request`, `views`, `authentication` | `AttributeError` in every request. |
| DRF | `Field._kwargs` | `fastdrf._classify`, `fastdrf._field_cache`, `fastdrf._field_copy`, `fastdrf._compiled` (django-fastdrf) | `AttributeError` when fields are classified or copied. |
| DRF | `Serializer._declared_fields`, `_readable_fields`, `_writable_fields`, `_read_only_defaults`, `_validated_data`, `_errors` | `serializers`, `aio`, `fastdrf.compiler`, `fastdrf.inputs`, `fastdrf.typed` | `AttributeError` in validation and representation. |
| DRF | `APIRequestFactory._encode_data` | `test` | `AttributeError` in the test client. |
| DRF | `view._ignore_model_permissions` | `contrib.permissions` | Read with a default: a view that sets it is checked for model permissions. |
| asgiref | `SyncToAsync.context_to_thread_executor`, `thread_sensitive_context` | `asgi` (`REQUEST_THREADS`) | `AttributeError` at the first request of the opt-in application. |
| drf-spectacular | the code constants of `drainage.isolate_view_method` | `utils` | Read with a default: a view method wrapped by `extend_schema_view` runs its prologue in a worker. |
| django-mongodb-backend | `DatabaseFeatures._supports_transactions` | `contrib.mongodb` | `AttributeError` at the first save with `ATOMIC_SAVE`. |
