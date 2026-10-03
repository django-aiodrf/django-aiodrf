# The tuned profile

aiodrf's defaults behave exactly like DRF. The options on this page trade some
of that behaviour for speed. Together they form the *tuned profile*, the
configuration aiodrf's benchmarks use to measure its fastest setup:

```python
FASTDRF = {  # django-fastdrf's optimizations, which aiodrf builds on
    "SERIALIZER_BACKEND": "msgspec",
    "SERIALIZER_BACKEND_PARITY": "strict",
    "SERIALIZER_BACKEND_FALLBACK": "error",
    "CACHE_SERIALIZER_FIELDS": True,
    "FIELD_COPY_MODE": "compiled",
}
AIODRF = {
    "REPRESENTATION_MODE": "inline",
    "REQUEST_THREADS": 32,
}
REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["fastdrf.msgspec.renderers.MsgspecJSONRenderer"],
}
# Views return fastdrf.response.DataResponse instead of Response.
```

Each option is independent of the others. Enable them one at a time and run
your own tests after each; the sections below describe what each option does,
how it differs from DRF, where it does not apply and what can go wrong.

## Option scopes

| Option | Project setting | View | Serializer (`Meta`) | Notes |
| --- | :-: | :-: | :-: | --- |
| `SERIALIZER_BACKEND` | `FASTDRF` | — | `serializer_backend` | Limited by `ALLOWED_SERIALIZER_BACKENDS` |
| `SERIALIZER_BACKEND_PARITY` | `FASTDRF` | — | — | Project-wide only |
| `SERIALIZER_BACKEND_FALLBACK` | `FASTDRF` | — | `serializer_backend_fallback` | |
| `CACHE_SERIALIZER_FIELDS` | `FASTDRF` | `serializer_field_cache` | `cache_fields` | A serializer's `Meta` takes precedence over the view |
| `FIELD_COPY_MODE` | `FASTDRF` | `serializer_field_copy_mode` | `field_copy_mode` | Requires the field cache |
| `REPRESENTATION_MODE` | `AIODRF` | — | — | Project-wide only |
| `MsgspecJSONRenderer` | `DEFAULT_RENDERER_CLASSES` (DRF) | `renderer_classes` | — | |
| `DataResponse` | — | returned by the handler | — | Per response |
| `REQUEST_THREADS` | `AIODRF` | — | — | Process-wide; used only by `aiodrf.asgi.get_asgi_application()` |

`SERIALIZER_BACKEND_PARITY` and `REPRESENTATION_MODE` cannot be limited to a
view or a serializer. If only some endpoints should represent on the event
loop, keep the default `"thread"` mode: it already represents loaded objects on
the event loop when it can prove that no query is needed (see
[inline representation](#representation-on-the-event-loop) below).

## Compiled serializers

`SERIALIZER_BACKEND`, `SERIALIZER_BACKEND_PARITY`, `SERIALIZER_BACKEND_FALLBACK`

**Behaviour.** django-fastdrf compiles a serializer's output, and the
validation of well-formed input, to msgspec or Pydantic when the result is known to be
identical to DRF's; aiodrf runs the compiled code in its async paths.
`SERIALIZER_BACKEND = "python"` compiles the same output
without either library, and leaves input to DRF. The [serializer backend guide](msgspec-pydantic.md) explains
the details.

**Differences from DRF.** With `"strict"` parity there are none: a serializer
whose output could differ is not compiled. `"fast"` parity accepts a few
documented differences, such as decimals rendered with `str()`.

**Limitations.** Strict parity compiles model-backed strings, integers, floats,
booleans, UUIDs, dates, times, datetimes, decimals output as strings, choices,
file fields, primary keys and slugs of forward and to-many relations, dotted
sources through foreign keys that cannot be null, and nested serializers for
forward and to-many relations. It leaves other fields to DRF, among them JSON,
method and custom fields, and serializers that define their own
`to_representation`, unless the fields are registered with
`fastdrf.registry` or delegated (`FASTDRF["DELEGATE_FIELDS"]`, which runs
them with their own code inside the compiled output). The [output section](msgspec-pydantic.md#output)
of the serializer guide has the complete list. msgspec or Pydantic must be
installed for their backends.

**Risks.** With `SERIALIZER_BACKEND_FALLBACK = "error"`, a serializer that
cannot be compiled raises `ImproperlyConfigured` instead of falling back to DRF.
An instance that a compiled serializer cannot read is represented by DRF either
way.
Use it only when every serializer is expected to compile, and exempt the others
with `Meta.serializer_backend_fallback = "drf"`. Run
`python manage.py aiodrf_inspect_serializers` to see which serializers compile
and why the others do not.

## Field cache

`CACHE_SERIALIZER_FIELDS`, `FIELD_COPY_MODE`

**Behaviour.** A `ModelSerializer` whose fields depend only on its class
builds them once; each instance receives its own copy, made with `deepcopy` or
with the faster `"clone"` and `"compiled"` copy plans.

**Differences from DRF.** None, as long as the serializer's declarations do not
change at run time.

**Limitations.** Serializers with field-building hooks (`__init__`,
`get_fields`, `build_field` and similar), with `Meta.depth`, or whose model
fields call project code (callable `choices`, for example) are built per
instance as in DRF.

**Risks.** Changes to model fields or `Meta` made after a serializer class was
first used, for example by a test that patches them, are not picked up. See
[selective serializer optimization](serializer-optimization.md).

## Representation on the event loop

`REPRESENTATION_MODE = "inline"`

**Behaviour.** Serializers produce their output on the event loop instead of
in a worker thread. By enabling it, you state that your serializers only read
data that is already loaded and never block.

**Differences from DRF.** None in the output.

**Limitations.** The setting applies to the whole project. Without it, aiodrf
still represents on the event loop whatever it can prove needs no query: model
instances whose serializer reads only loaded columns, `select_related`
relations and `prefetch_related` lists of up to 32 objects, and compiled
encoders over loaded columns.

**Risks.** Anything that performs I/O during representation raises Django's
`SynchronousOnlyOperation` instead of running in a thread:

- a relation missing from `select_related` or `prefetch_related`;
- a model property or `SerializerMethodField` that queries the database or
  calls a service;
- a file field whose storage performs I/O to build a URL, such as
  django-storages' S3 backend when it refreshes its credentials;
- the many-to-many relations of an object just created by a serializer that
  also has async fields. (Serializers without async fields represent a newly
  created object in the worker thread that saved it.)

Make sure your tests serialize every relation. A large response also occupies
the event loop while it is produced, which delays other requests handled by the
same process.

## `MsgspecJSONRenderer`

**Behaviour.** Renders JSON with `msgspec.json`.

**Differences from DRF's `JSONRenderer`.** `timedelta` values are rendered as
ISO 8601 durations instead of seconds and `bytes` as base64; NaN and infinity
become `null` instead of raising an error; plain `Decimal` values keep their
digits (`1.50` where DRF writes `1.5`). `DecimalField` output is a string in
both renderers when `COERCE_DECIMAL_TO_STRING` is enabled. Indented output (the
`indent` media type parameter, the browsable API) uses DRF's renderer.

**Risks.** Clients that parse the forms listed above. msgspec must be
installed.

## `DataResponse`

**Behaviour.** A handler returns `fastdrf.response.DataResponse(data, status,
headers, content_type)` instead of DRF's `Response`. The view renders it
immediately with DRF's JSON renderer and returns a Django `HttpResponse` with
the same status, content and headers DRF would produce.

**Differences from DRF.** It is not a DRF `Response`: there is no `render()`
step, so `process_template_response` middleware does not see it; of DRF's
attributes it only has `renderer_context`; and `data` is `None` once the
response is rendered, because the payload is released before the response is
sent. In tests, read the content with `response.json()`.

**Limitations.** When the negotiated renderer is not a JSON renderer (the
browsable API, templates), or when the view defines `finalize_response`, the
view converts it to a regular DRF `Response`, with no speed benefit. Returned by
a view that is neither an aiodrf view nor uses django-fastdrf's
`DataResponseMixin`, its content raises `ContentNotRenderedError`.

**Risks.** Middleware, decorators or tests that read `response.data` or other
`Response` attributes.

## Request threads

`REQUEST_THREADS`

**Behaviour.** For each request, Django's ASGI handler starts a thread to run
synchronous code and waits for it to finish. With this setting,
`aiodrf.asgi.get_asgi_application()` keeps those threads for later requests:
each thread serves one request at a time, and at most the configured number are
kept idle.

**Differences from Django.** Thread-local state outlives a request, as in a
threaded WSGI server. This includes `threading.local` values and, when
`CONN_MAX_AGE` keeps connections open, one database connection per kept thread
and database.

**Limitations.** Applies only to the application returned by
`aiodrf.asgi.get_asgi_application()`, for the whole process.

**Risks.** Code that relies on each request running in a new thread, and the
memory used by idle threads. The feature uses asgiref's executor mapping, which
is not a documented asgiref API; it is tested with asgiref 3.8.1 and 3.12.1.

## Adopting the profile

1. Measure your own workload first, as described in the
   [performance guide](performance.md). Benchmark gains depend on the benchmark's
   scenarios.
2. Start with the options whose risks you can check most easily:
   `MsgspecJSONRenderer` (check your clients), the field cache (check that your
   serializers are static) and `DataResponse` on selected JSON endpoints.
3. Enable `SERIALIZER_BACKEND` with strict parity and the default `"drf"`
   fallback. Switch the fallback to `"error"` only after
   `aiodrf_inspect_serializers` shows that everything you expect compiles.
4. Enable `REPRESENTATION_MODE = "inline"` last, once your tests serialize every
   relation.

## Third-party packages

With the options above and `SERIALIZER_BACKEND_FALLBACK = "drf"`, the tested
[third-party packages](ecosystem.md) work unchanged, with two exceptions
caused by inline representation: S3 file URLs from django-storages, and
serializers that read relations the view does not load (see the risks of
[inline representation](#representation-on-the-event-loop)).

With `SERIALIZER_BACKEND_FALLBACK = "error"`, the serializers of these packages
raise `ImproperlyConfigured`, because their fields or serializers cannot be
compiled: django-money (`MoneyField`), django-phonenumber-field, django-taggit
(`TagListSerializerField`), django-polymorphic, drf-flex-fields,
django-restql, django-rest-framework-json-api (`ResourceRelatedField`),
djangorestframework-dataclasses, the image fields of drf-extra-fields,
django-pydantic-field (`SchemaField`), and the embedded models and arrays of
django-mongodb-backend. Its `ObjectId` fields compile with
`aiodrf.contrib.mongodb` installed. Set `Meta.serializer_backend_fallback = "drf"` on those
serializers, or keep the project-wide default of `"drf"`.
