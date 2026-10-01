# msgspec and pydantic

Four independent integrations use these libraries. Serialization and validation
remain CPU work; backend selection does not make them asynchronous. Performance
depends on eligibility, payload size and the selected compatibility mode.

| Feature                              | Contract                                                                                                                                                                                      |
| ------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Serializer backend (section 1)       | An existing DRF serializer stays the definition. The msgspec and pydantic backends compile output and recognize a conservative input subset when the result is known to equal DRF's; the python backend compiles output only, without a dependency; otherwise DRF does the work. |
| Schema-first serializers (section 2) | `MsgspecSerializer` / `PydanticSerializer`: a Struct or `BaseModel` is the definition; the library's rules apply, not DRF's.                                                            |
| JSON codec (section 3)               | `MsgspecJSONRenderer` / `MsgspecJSONParser`, independent of the other two.                                                                                                                |
| Conversion (section 5)               | `manage.py aiodrf_convert` writes a schema for a serializer, or a serializer for a schema.                                                                                                  |

Install `django-aiodrf[msgspec]` or `django-aiodrf[pydantic]`. aiodrf imports neither
unless it is used; selecting a backend that is not installed is reported by
`manage.py check` (`aiodrf.E004`).

## 1. The serializer backend

```python
AIODRF = {
    "SERIALIZER_BACKEND": "msgspec",  # "drf" (default), "msgspec", "pydantic", "python"
    "SERIALIZER_BACKEND_PARITY": "strict",  # or "fast", for output
    "SERIALIZER_BACKEND_FALLBACK": "drf",  # or "error"
}


class AuthorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Author
        fields = ["id", "name"]
        serializer_backend = "msgspec"  # overrides the setting; "drf" opts out
        serializer_backend_fallback = "error"  # overrides the setting
```

The serializer object is DRF's throughout: `fields`, context, `errors`,
`validated_data`, `data`, `save()`, `many=True` and schema generation are
unchanged. The backend applies where aiodrf produces the result: generic
views, `aio.data` / `serializer.adata()` and `aio.is_valid` /
`serializer.ais_valid()`. Reading `serializer.data` or calling `is_valid()`
directly is DRF's code.

A serializer is compiled as a whole, per direction, or not at all. When it
cannot be, DRF does the work; with the fallback set to `"error"`,
`ImproperlyConfigured` names the reason instead: a field the backend cannot
express, a custom list serializer, more field sets than the cache keeps.

### Output

Compiled in `"strict"` parity, where the output is identical to DRF's:

- `ModelSerializer` fields backed by concrete model fields of the matching
  type: strings, integers (DRF 3.17's `BigIntegerField` too, unless coerced
  to a string), floats, booleans, UUIDs (`hex_verbose`), dates and times in
  ISO 8601, choices whose keys have the model field's type. UUIDs, dates and
  times are output by DRF's own conversion, so a value that is still the text
  it was set to (`Model(code="...")`, before the row is read again) comes out
  unchanged, as DRF outputs it;
- datetimes in ISO 8601 and `DecimalField`s output as strings (DRF's
  default, `COERCE_DECIMAL_TO_STRING`), with DRF's output for any value:
  datetimes converted to the active time zone (or left naive without
  `USE_TZ`), with `Z` for a zero offset; decimals quantized to their places in
  the thread's decimal context with the field's digits and rounding, and
  normalized when the field says so, with the same errors. The time zone and
  the decimal context are read once per output rather than once per value,
  and the backend formats datetimes itself. Rare values (a naive datetime to
  make aware, an offset in seconds, an overflow, a string) and localized
  decimals are converted by DRF's own code. A `DateTimeField` with a
  `default_timezone` of its own, a `DecimalField` that outputs `Decimal`
  objects and a subclass that overrides `quantize()` stay on DRF;
- `PrimaryKeyRelatedField` on a forward foreign key, read from `<fk>_id`,
  when the key is a string or an integer;
- the column of a forward foreign key itself (`"<fk>_id"` in `Meta.fields`,
  read with a `ReadOnlyField`, or a declared field), as a field of the key's
  target type. DRF outputs the value of a `ReadOnlyField` or a
  `PrimaryKeyRelatedField` unchanged: a UUID or a date stays a Python object
  in `.data`, an integer in a `FloatField` stays an integer. Such fields
  compile only when the value is a string, an integer or a boolean;
- the primary keys of a many-to-many field (either side) or a reverse foreign
  key (`PrimaryKeyRelatedField(many=True)`, what `ModelSerializer` builds for
  a many-to-many field), when the keys are strings or integers;
- `SlugRelatedField` on a forward foreign key, a many-to-many field or a
  reverse foreign key, when the slug is a column of the related model (not
  a path such as `author__name`) holding strings, integers or booleans;
- `ModelField`, which `ModelSerializer` builds for a `GeneratedField`, when
  the value is a string, a number or a boolean;
- `FileField` and `ImageField` on the model's file fields: the file's URL,
  made absolute with the request in the serializer's context, or its name
  with `use_url=False`. Building a URL can query the storage, so such a
  serializer is never represented on the event loop in thread mode;
- a field whose dotted source follows foreign keys that cannot be null to a
  column of the related model (`CharField(source="author.name")`). A missing
  related row gives `None`, as in DRF;
- a declared string, number, boolean, UUID, date, time, datetime or decimal
  field reading a value of the instance alone, which the model's class does
  not define: an annotation (`annotate(book_count=Count("books"))`) or a
  value the view set. The value is represented as the DRF field represents
  it, whatever its type, and a method stored there is called, as DRF calls
  it. An instance without the value is DRF's, which skips the field;
- with `aiodrf.contrib.mongodb` installed, `ObjectId` columns through
  django-mongodb-extensions' `ObjectIdField` and aiodrf's
  `ObjectIdPrimaryKeyRelatedField`, single and `many=True`;
- nested serializers on forward foreign keys, recursively;
- nested `many=True` serializers on a many-to-many field (either side) or a
  reverse foreign key, when the list serializer is DRF's own and the child
  compiles.

msgspec and pydantic serializers output with their own schema: the compiler
neither compiles nor refuses them, whatever `SERIALIZER_BACKEND_FALLBACK` says.

Related managers are read like DRF's `ManyRelatedField` and `ListSerializer`
read them: `manager.all()`, the prefetched objects when the queryset
prefetched them (in the prefetch's order), a query otherwise, made where DRF
would make it (the worker thread in thread mode). Foreign keys in a dotted
source are read as DRF reads them, joined by `select_related()` or queried.
The objects, their order and the number of queries are DRF's:
`select_related()` and `prefetch_related()` matter exactly as much as they do
for DRF.

A compiled field outputs `None` as `null` even when the model field is not
nullable, as DRF does.

The compiled class represents instances of the serializer's model (a list or
queryset of them for `many=True`): they have every attribute it reads.
Anything else, such as a dict or the rows of `QuerySet.values()`, is DRF's:
DRF outputs a field's default, or skips a field that is not required, when
the key or attribute is missing. With
`serializer_backend_fallback = "error"` such a source raises. In `"fast"`
parity a plain `Serializer` reads any object but a mapping.

An instance can still hold what the compiled class cannot read: a forward
relation whose row does not exist (a `db_constraint=False` key, a row deleted
meanwhile), a deferred field of a deleted row, or the related manager of an
unsaved instance, which DRF outputs as `None` or `[]`; a value of another type
than the model field's, set by the project, is converted by DRF's field (`5` in
a `CharField` is `"5"`). In `"strict"` parity, when the compiled class fails
with its backend's validation error, DRF represents the source, with DRF's
output or DRF's exception, whatever `serializer_backend_fallback` says: what
one instance holds is not a serializer that cannot be compiled. The backends
take any exception raised while reading an attribute (a database error, a
query a profiler refuses) for a missing attribute; DRF's read raises it as
itself. DRF reads the source again, so strict parity compiles only
reads of Django's code: Django's descriptors of Django's field classes, and
relations read lazily through managers whose `get_queryset()` and `all()` are
Django's. A model field class of the project's or a third party's, an attribute
the model defines over a field, or such a manager keeps the serializer on DRF.
The second read repeats at most Django's query of a lazy relation. `"fast"`
parity does not read again: the backend's error (`msgspec.ValidationError`,
`pydantic.ValidationError`, `aiodrf.contrib.compiler.UnreadableValue`) is raised where DRF outputs `None`, converts the
value or skips a missing attribute.

Everything else keeps the serializer on DRF: `SerializerMethodField`, a nested
`many=True` or `PrimaryKeyRelatedField(many=True)` on anything but a related
manager (a list property, the list a `Prefetch(to_attr=...)` sets: DRF skips a
field whose attribute is missing, the compiled class cannot), embedded models
and arrays of django-mongodb-backend, a dotted source through a nullable
foreign key (DRF then skips the key or outputs the field's default), through a
related manager or to a property, `source="*"`, custom fields, an overridden
`to_representation` or `get_attribute`, or one assigned to the serializer, the
list serializer or a field instance, a list serializer with its own
`to_representation` (at the top or nested), a nested serializer whose
`Meta.model` is not the related objects' model, fields without a model field,
choices with keys of another type than the model field's, and JSON (DRF leaves
values such as `Decimal` or tuples to its encoder). With msgspec, several
fields may read the same attribute or relation: it is read once and converted
for each of them. `"fast"` additionally compiles `DecimalField`s that output
`Decimal` objects (as `str(value)`, not quantized), fields of a plain
`Serializer` and JSON fields; its output can differ from DRF's for these.

Which compiled class a serializer uses is found in one of two ways:

- *By class*, when its fields are a function of its class: the class and
  every nested serializer it declares build their fields with DRF's code
  alone (no `__init__`, `get_fields()`, `get_field_names()`, `build_*()` or
  `bind()` of the project's, no `Meta.depth`, no model field whose
  building runs the project's code (implementation guide, section 3), only
  DRF's own field classes declared, children of collections and to-many
  relations included), and the instance was
  created with the usual arguments (`instance`, `data`, `context`,
  `partial`) and has nothing of the class shadowed: no fields read yet (they
  may have been edited), no method assigned. Such a serializer is analyzed
  once per class and later instances build no DRF field at all; one that
  does not compile is not analyzed again either. Changes made to a static
  serializer instance are still respected. After validation its fields
  exist on the instance; a generic view's `create` and `update` still use the
  class's compiled class when the view built, validated and saved the
  serializer with framework code alone (no `get_serializer*` or `perform_*`
  of the project's) and the serializer's class defines no methods, since
  nothing could have edited the fields.
- *By field set* otherwise: fields removed per instance and changed field
  options are part of the cache key, so a serializer with dynamic fields
  compiles one variant per field set, at most 32 per class, and DRF serves
  the rest. This builds the instance's fields.

The backend and parity are part of both keys; compiled classes are dropped
when `REST_FRAMEWORK` changes, which the analysis reads. A class with its own
`to_representation` is refused without reading the instance's fields, also
when they exist already, as after validation.

`aio.data` on a model instance usually represents it in a worker thread
(`REPRESENTATION_MODE = "thread"`), since reading an attribute may query.
When the class's encoder reads only columns of the model, and the instance
has each of them loaded (none deferred), nothing can query and the output is
produced on the event loop, without the hop; relations and nested
serializers keep it.

### The python backend

`"SERIALIZER_BACKEND": "python"` compiles output with the same analysis, the
same strict parity and the same declines as msgspec and pydantic, into plain
Python readers: one per field and a dict per instance, without either library.
It accepts what the msgspec backend accepts for each field type; a value of
another type (a string set on an integer field) or an attribute it cannot
read makes DRF represent that source. Input is validated by DRF, as without a
backend.

On a `ModelViewSet` of a five-field model, one request of `list` (20 rows) and
`retrieve` took 3.55 M and 2.05 M instructions with it, against 5.57 M and
3.12 M with DRF's serializer and 3.40 M and 2.04 M with msgspec, the field
cache on. Its readers are as fast as msgspec's for one object and within
about 10 % on lists; where msgspec is not wanted it gives the compiled
output's gain without the dependency.

### Datetime contracts

Compiled DRF serializers and explicit vendor schemas have different contracts.
The compiler's supported datetime path calls DRF timezone/format conversion;
unsupported datetime options are left to DRF. Time zone overrides, DST
transitions, naive values, fractional seconds and out-of-range values produce
DRF's output with both backends.

Pydantic and msgspec schemas used directly follow their library's rules for
datetime input and output, not Django's time zone conversion: datetime objects,
ISO strings, invalid dates and single or list output behave exactly as with the
library itself. Pydantic's strict validation of Python objects rejects ISO
strings in a dictionary produced by a JSON parser; msgspec's rules differ. If a
schema should accept those strings, set `Meta.strict = False` on it and test the
resulting coercion of its other fields. The adapter never changes strictness or
falls back to DRF validation on its own.

### Input

`aiodrf/contrib/inputs.py` compiles a *recognizer*, not a second validator.
It accepts only input for which DRF would return exactly the same
`validated_data`: a JSON `dict` (or `list` for `many=True`) whose values
already have the types DRF converts to, strings already trimmed, numbers
within their limits. Whatever it does not accept, DRF validates as usual, so
every coercion (`"12"` for an integer), every error
message and code is DRF's. Both backends use this analysis, selected by
`SERIALIZER_BACKEND` or `Meta.serializer_backend`. Both backends recognize
canonical UUID strings, ISO dates and naive ISO times. Pydantic uses narrow
text conversions for these fields before strict validation; unrelated coercions
remain disabled. A time is read as Django reads it (`time.fromisoformat`),
digits beyond microseconds truncated; msgspec's own parser would round them,
up to the next minute or midnight. This does not change schema-first serializers' strictness.
The pydantic input path does not require msgspec to be installed.

Input rejection always falls back to DRF, including when the output
compilation policy is `SERIALIZER_BACKEND_FALLBACK = "error"`. That setting
governs output eligibility; a recognizer declining a value is not a validation
error. Schema-first serializers still use their schema's own validation rules.

A serializer takes part when nothing but these field classes decides the
result, by exact class: `BooleanField`, `IntegerField`, `FloatField`,
`CharField`, `ChoiceField` (string or integer keys), `UUIDField`, `DateField`
and `TimeField` (ISO 8601 input only), nested serializers and nested `many=True`, with
`required`, `allow_null`, `allow_blank`, constant defaults, `source`
(dotted too), `partial=True`, and min/max length and value validators with
constant limits (several of one kind combine to the stronger one, as DRF
runs them all; `allow_blank` keeps the empty string and nothing else exempt
from `min_length`). It does not when there is a `validate()` or
`validate_<field>()`, an async member, a serializer-level validator
(unique-together), a relation, a subclassed field, a callable default or
limit, `source="*"`, or any other validator (`UniqueValidator`,
`RegexValidator`). Form input (`QueryDict`) always goes to DRF.

`ListField`, `DictField` and `HStoreField` are recognized recursively when their
children satisfy the same rules. Child nullability, validation bounds and string
rules remain effective; changing a child or assigning a validation hook cannot
reuse an incompatible cached plan. Untyped containers retain DRF validation.
Times with offsets retain DRF validation because Django treats time strings and
Python time objects differently. See the [type coverage matrix](../reference/serializer-type-coverage.md)
for intentional fallbacks and schema-native alternatives.

Both properties are verified against DRF with generated input and combinations
of field options and validators: whatever is recognized, DRF accepts, with the
same values and types. Backend model
instances and custom container objects are not recognized as JSON input.

### Measuring compilation

Measure cold compilation, warm output and fallback-heavy inputs separately.
A static serializer's plan can be reused without reconstructing its fields;
dynamic fields and custom hooks still require inspection. Fallback has a cost,
so compiler eligibility is not itself evidence of an endpoint improvement.
The [performance guide](performance.md) describes suitable measurement tools.

### Compilation diagnostics

```console
python manage.py aiodrf_inspect_serializers [--backend msgspec|pydantic] [--parity strict|fast] [--format text|json]
```

lists the serializers of the project's API views with, for each direction,
`compiled` or `DRF: <reason>` for the selected installed backend. It
instantiates serializers without a request, so one whose fields depend on
context is reported as it looks without it, and one that cannot be
instantiated is listed as such; its constructor and field-building hooks
must be safe to run without a request. No request values are included.

A schema serializer (`MsgspecSerializer`, `PydanticSerializer`) is reported
as `schema: msgspec validates and represents it` (code `schema_serializer`):
its schema does the work in both directions, not the backend. A
`SchemaViewMixin` view is inspected through the serializer its
`input_schema`/`output_schema` pair builds, and a bare Struct or model set as
`serializer_class` through the class it is adapted to.

With `--format json`, input and output eligibility are reported separately.
Eligibility means that a recognizer can be built, not that it will accept
every value. Every record has `serializer` (a dotted path, or null),
`inspected` and `usages` (the endpoints: `path`, `method`, `action`, and a
`note` when the view's `get_serializer_class()` may choose another serializer
at request time). A record with `inspected: false` has a `reason`: the
serializer could not be instantiated without a request, its fields or its
analysis failed without one (a `get_fields()` reading `context["request"]`;
the reason starts with "could not be analyzed"), the view chooses its
serializer in `get_serializer_class()`, a generic view declares none, or the
view is not a generic view. Inspected records include `scope: "instance"`,
the backend and parity, and per direction `eligible`, `code` and `reason`.
The report is static: it says what could compile, not what requests did
(a serializer instance can still fall back to DRF, for example when a method
is assigned to one of its fields). Codes include
`eligible`, `custom_hook`, `custom_validator`, `async_validation`,
`dynamic_default`, `unsupported_source`, `unsupported_relation`,
`custom_list`, `model_required`, `backend_not_installed` (the selected
backend is missing: input is not inspected, output only for what keeps it on
DRF) and the conservative `unsupported_field` category; consumers should
branch on the code, not parse the reason.
`compiler.report_details()` and `inputs.report_input_details()` return the
same structured decision for one serializer instance; the older `report()`
and `report_input()` return a reason or `None`.

## 2. Schema-first serializers

Install `django-aiodrf[pydantic]` or `django-aiodrf[msgspec]`. Define native
`pydantic.BaseModel` or `msgspec.Struct` classes; no separate field declarations
or DRF-to-schema compilation are required. This mode delegates input conversion
and output representation to the selected library. It has no DRF validation
fallback and is distinct from compiling an existing DRF serializer.

```python
from aiodrf.contrib.msgspec import MsgspecSerializer


class BookSerializer(MsgspecSerializer):
    class Meta:
        schema = BookSchema  # a msgspec.Struct
        # or: input_schema = BookIn; output_schema = BookOut
        model = Book  # optional: create()/update() write its fields
        strict = True  # False: accept "12" for an int, like DRF
```

`PydanticSerializer` is configured the same way. A bare Struct or model
class can be a view's `serializer_class`; aiodrf wraps it (one serializer
class per schema, at most 1024 kept). These are DRF serializers, so generic
views, `many=True`, `OPTIONS` metadata and drf-spectacular work, but the
validation rules, coercions and messages are the library's:

- `validated_data` is a dict of the schema's fields. Nested values stay
  Struct or model instances; `create()`/`update()` with `Meta.model` assign
  the fields as given (a foreign key as `author_id`) and set to-many
  relations (a list of primary keys or instances) after the save, as DRF's
  `ModelSerializer` does; nested writes are not handled.
- msgspec stops at its first error (code `invalid`, or `required`); pydantic
  reports all of them with its own codes. Both arrive as DRF
  `ValidationError` trees, lists keyed as `LIST_SERIALIZER_ERRORS_AS_DICT`
  says. The errors of an array input itself (an `array_like` Struct, a list
  `RootModel`) are keyed by index on every DRF version, since a serializer's
  errors are a dict.
- `.fields` are read-only DRF fields describing the *output* schema, for
  `OrderingFilter`, metadata and the browsable API. DRF's HTML form skips
  read-only fields, so the browsable API offers its raw-data form only.
  Posted form data (`QueryDict`) is validated with lenient coercion, and
  collection fields keep repeated values.
- What a schema accepts is the backend's, not DRF's fields'. A `float` field
  accepts `1e400` (infinity) under both backends, and pydantic also accepts
  the strings `"nan"` and `"inf"`; a `str` field accepts a lone surrogate
  (`"\ud800"`). DRF's `FloatField` and `CharField` refuse these with a 400;
  a schema lets them through validation, and DRF's renderer then fails on
  them (a 500). Refuse them in the schema: `allow_inf_nan=False` in a
  pydantic model's `model_config`, `Annotated[float, msgspec.Meta(ge=...,
  le=...)]` in a Struct. `MsgspecJSONParser` refuses non-finite numbers and
  lone surrogate escapes when it parses the body (section 3).
- The two backends accept different input for the same annotation, so the
  same body can be a 400 under one and a 200 under the other:

  | Input | msgspec | pydantic |
  | --- | --- | --- |
  | `UUID` as `{12345678-...}` or `urn:uuid:12345678-...` | refused | accepted |
  | `bytes` as `"YQ"` | refused (not valid base64) | accepted, as the bytes of the text |
  | `float` as `"nan"` | refused | accepted |

  Output differs too: a `timedelta` of one hour is `"PT3600S"` under msgspec
  and `"PT1H"` under pydantic (DRF's `DurationField` writes `"01:00:00"`).

### In views

Three ways, from least to most explicit:

- `serializer_class = BookSchema` on a generic view: the bare Struct or model
  validates and represents.
- `aiodrf.contrib.typed.SchemaViewMixin` with `input_schema` and
  `output_schema`: request bodies are validated with one, responses
  represented with the other (either alone does both). In a generic view the
  pair's `create()`/`update()` write `queryset.model`: the view's `queryset`
  attribute, given to the class or to `as_view(queryset=...)`. A
  `get_queryset()` override does not change the model written.
- A `MsgspecSerializer`/`PydanticSerializer` subclass, for `Meta` options
  (`strict`, `partial_schema`).

```python
from aiodrf.contrib.typed import SchemaViewMixin


class BookViewSet(SchemaViewMixin, viewsets.ModelViewSet):
    queryset = Book.objects.all()
    input_schema = BookIn
    output_schema = BookOut

    def perform_create(self, serializer):
        book_in = serializer.validated_object  # a BookIn
        serializer.save(owner=self.request.user)


class NewBook(SchemaViewMixin, APIView):
    input_schema = BookIn
    output_schema = BookOut

    async def post(self, request):
        body = await self.aget_validated_body()  # a BookIn; DRF's 400 if invalid
        book = await Book.objects.acreate(**body.model_dump())
        return self.schema_response(book, status=201)
```

`validated_data` stays DRF's dict, so `save()` and `perform_create` work
unchanged; `serializer.validated_object` is the input schema's instance (the
partial schema's for PATCH). `get_validated_body()` is the synchronous form
for synchronous handlers. `schema_response(data, many=False, **kwargs)`
represents with the output schema and returns a `Response`; it reads model
instances by attribute on the calling thread, so relations it reads must be
loaded. drf-spectacular documents the request body from `input_schema` and
the response from `output_schema`, for generic views and for `APIView`s.

The static serializer is resolved when the URL is built (`as_view`): the
bare schema or the pair becomes its serializer class then, and a request
costs one dictionary lookup. Input
and output schemas must come from one library. A serializer that
`get_serializer_class()` chooses per request is resolved when the view
builds it.

### Native validation and serialization hooks

Pydantic field/model validators, field/model serializers, aliases, computed
fields and configuration belong on the `BaseModel`. The serializer passes its
DRF `context` to Pydantic validation and serialization, including `many=True`
and partial output. A generic view supplies `request`, `view` and `format`;
direct serializer construction can supply application values:

```python
from pydantic import BaseModel, FieldSerializationInfo, field_serializer

from aiodrf.contrib.typed import adapt


class Price(BaseModel):
    amount: int

    @field_serializer("amount")
    def display(self, value: int, info: FieldSerializationInfo) -> str:
        return f"{info.context['currency']} {value}"


serializer = adapt(Price)(data={"amount": 20}, context={"currency": "EUR"})
await serializer.ais_valid(raise_exception=True)
data = await serializer.adata()  # {"amount": "EUR 20"}
```

Context stays on the serializer's adapter; shared schema caches do not retain
requests. Construct one serializer per operation. Pydantic's own output rules
apply: for example, `extra="allow"` retains extra input values in
`validated_data`, while `Field(exclude=True)` excludes an output field. Review
extra-field policies before accepting data passed to `create()` or `update()`.

Msgspec custom types use the following optional `Meta` callables:

| Option | Signature | Purpose |
| --- | --- | --- |
| `dec_hook` | `(target_type, value) -> object` | Convert a custom input type; raise `ValueError` for invalid input |
| `enc_hook` | `(object) -> JSON-compatible value` | Represent a custom output type |
| `schema_hook` | `(target_type) -> dict` | Describe the custom type for JSON Schema/OpenAPI |

All default to `None`. Hooks are serializer-local configuration, not global
msgspec patches. A hook must reject types it does not handle. The same conversion
hooks participate in nested output projection, which keeps subclass-only fields
out of a declared base schema's response. Such projection can invoke hooks more
than once; keep them deterministic and free of side effects.

```python
class ReferenceSerializer(MsgspecSerializer):
    class Meta:
        schema = BookReference
        dec_hook = decode_reference
        enc_hook = encode_reference
        schema_hook = reference_schema
```

The [typed schemas example](../../examples/typed-schemas/README.md) includes
complete hook implementations, Pydantic context, request errors and Swagger.
Unsupported custom types fail explicitly; aiodrf does not silently change to
DRF validation. `schema_hook` must describe the actual wire value—it does not
implement conversion or validation.

### Root values and execution policy

`PydanticSerializer` preserves a `RootModel`'s array or scalar output and a
`model_serializer`'s return shape. Msgspec `array_like=True` Structs retain array
output. `validated_data` remains attribute-keyed (`{"root": ...}` for a
RootModel); use `validated_object` when application code needs the native object.
For `many=True`, DRF returns a list of attribute mappings; it does not expose a
list-level `validated_object`. RootModels require an explicit `partial_schema`
because field-level PATCH has no general meaning for a root value.

The normal path still parses the request body, validates a native schema,
produces JSON-compatible Python values, and renders a DRF response. It does not
promise a zero-copy `model_validate_json`/encoder pipeline. DRF negotiation,
permissions, exception handling and response hooks remain active.
Options specific to a vendor's JSON encoder are not automatically renderer
options; test the final response bytes with the selected DRF renderer. Output
conversion may run schema validators again, especially for mappings and list
output. Validators and conversion hooks should not perform side effects.

Native validators are synchronous vendor callbacks, not async hooks. Await
external I/O in the view or an aiodrf serializer hook. Async operations keep
unclassified callbacks in Django's thread-sensitive worker by default. Only
select `VALIDATION_UNKNOWN="inline"` or `REPRESENTATION_MODE="inline"` for
application code known not to block or load lazy relations. A thread boundary
protects loop responsiveness but does not accelerate GIL-bound Python work.

Msgspec value extraction uses its shallow `structs.asdict()` API. Stable Struct
metadata and nested-output classification share a bounded cache; bound DRF fields
remain independent per serializer. Pydantic reuses a bounded cache of list
`TypeAdapter`s. Neither cache stores request data. Metadata is for classes fixed
at application startup; dynamically rebuilding an already-adapted schema is not
a supported cache-invalidation mechanism.
Warm validation also avoids constructing synthetic read-only fields just to
collect defaults they do not define. Materialized or customized fields retain
DRF's default handling; this optimization does not suppress user validators.

### Relationship to third-party adapters

[`drf-pydantic`](https://github.com/georgebv/drf-pydantic) generates DRF fields
from Pydantic model declarations. Its optional Pydantic validation runs after
DRF field validation. That contract is useful when a project needs generated
DRF serializers; it is different from delegating validation and representation
directly to Pydantic. aiodrf therefore does not add it as a dependency of the
native serializer path.

[`django-msgspec-field`](https://github.com/quertenmont/django-msgspec-field)
provides schema-backed Django model/form fields and DRF field/parser/renderer
integrations. That persistence-oriented scope does not replace aiodrf's existing
whole-serializer, async operation and drf-spectacular contracts. Native contrib
adapters call msgspec directly rather than introduce another adapter layer.
These are design distinctions, not universal speed comparisons or support
guarantees for third-party packages.

### Allowed serializer backends

```python
AIODRF = {"ALLOWED_SERIALIZER_BACKENDS": ["drf", "pydantic"]}  # default: all three
```

`"drf"` is a DRF serializer (compiled by `SERIALIZER_BACKEND` or not),
`"msgspec"` a Struct or `MsgspecSerializer`, `"pydantic"` a model or
`PydanticSerializer`. A view whose serializer or schemas are of another kind
fails when its URL is built, with `ImproperlyConfigured` naming the view;
`manage.py check` lists every such view at once (`aiodrf.E005`), and a
serializer chosen per request is checked when it is built. The setting does
not affect `SERIALIZER_BACKEND`, which only decides how DRF serializers are
executed.

### PATCH

For `partial=True` a schema with every field optional is derived from the
input schema, but only when nothing is lost by that: a derived class runs
none of the schema's own validation. With a msgspec `__post_init__`, or
pydantic validators, `model_post_init` or `validate_default` (in the model's
config or on one field), set `Meta.partial_schema`; without it a partial
update raises `ImproperlyConfigured` before anything is validated. Rules that
need the complete object belong in the view, after the stored state and the
patch are combined. Compiled DRF serializers (section 1) use DRF's `partial`
semantics and need none of this.

A derived msgspec schema keeps the Struct's names, unknown-field policy and
tag (`tag`, `tag_field`): a wrong tag is rejected as in full validation. An
`array_like` Struct needs `Meta.partial_schema`, since a shorter array cannot
say which fields it leaves out.

`.data` of partial input before a save holds the given fields only,
represented by the output schema: pydantic field serializers, serialization
aliases and excluded fields apply, and computed fields are left out. An output
model with a `model_serializer`, or an `array_like` output Struct, cannot
represent part of an object and raises `TypeError`; represent the saved
instance instead.

### pydantic aliases

Form `QueryDict` input preserves repeated values for collection fields with
string aliases, string members of `AliasChoices`, and single-segment
`AliasPath` aliases. Population by the field name follows the model's own
`populate_by_name` / `validate_by_name` configuration; aiodrf does not enable it.
Nested alias paths are not reconstructed from flat form keys. They produce the
backend's normal missing-field validation result (or remain omitted in PATCH),
not an exception while inspecting aliases. JSON mappings keep normal pydantic
alias-path behavior. Msgspec fixed-length tuple fields also preserve repeated
form values.

Output uses serialization aliases (`by_alias=True`) in single objects, lists
and the synthetic `.fields`, because that is what the response schema
documents; input uses pydantic's validation schema and aliases. Test wire names explicitly
when migrating a model that declares a `serialization_alias`.

### OpenAPI

With drf-spectacular installed, request bodies are documented from the input
schema (an explicit `Meta.partial_schema` for PATCH) and responses from the
output schema. Components are identified by their shape, so a pydantic model
that validates and serializes differently (a `serialization_alias`) is
documented as two components, `Item` for responses and `ItemRequest` for
requests, nested or not; two different classes that share a name are reported
by spectacular. pydantic and
msgspec write JSON Schema 2020-12, which OpenAPI 3.1 is; for OpenAPI 3.0
(spectacular's default `OAS_VERSION`) the schemas are rewritten to its
dialect: `nullable`, boolean `exclusiveMinimum`/`exclusiveMaximum`, `enum`
for `const`, and a tuple's items as any of its types (with a warning). A
generic model's component is named as pydantic names it (`Page_int_`).
See the [ecosystem guide](ecosystem.md#aiodrf-schema-extensions).

## 3. JSON renderer and parser

`MsgspecJSONRenderer` renders on the event loop after the payload check
DRF's `JSONRenderer` gets (implementation guide section 5): data made of plain
values renders inline, anything its encoder hook would evaluate (a lazy
translation, a QuerySet) renders in the request's thread. msgspec `Struct`s
and dataclasses in the data are not plain values and cost that one hop.
It differs from DRF's encoder for these values:

| Value | `MsgspecJSONRenderer` | DRF's `JSONRenderer` |
| --- | --- | --- |
| `timedelta(hours=1)` | `"PT3600S"` (ISO 8601 duration) | `"3600.0"` |
| `b"ab"` | `"YWI="` (base64) | `"ab"`; an error for bytes that are not UTF-8 |
| `float("nan")`, `float("inf")` | `null` | an error |
| `Decimal("1.50")`, `Decimal("2")`, `Decimal("-0")`, `Decimal("1E+30")` | `1.50`, `2`, `-0`, `1E+30` | `1.5`, `2.0`, `-0.0`, `1e+30` |
| `1e300`, `1e-7` | `1e300`, `1e-7` | `1e+300`, `1e-07` |
| `time(1, tzinfo=UTC)` | `"01:00:00Z"` | an error |

Each is valid JSON of the same value, except the rows where DRF raises.
Data msgspec cannot encode as DRF does goes through DRF's renderer, so its
bytes, or its error, are DRF's: a dict with a `True`, `False` or `None` key
(`{"true": 1}`), and a `Decimal` NaN or infinity (DRF's `ValueError`, where
msgspec would write a bare `NaN` token). Indented output (the browsable API,
`; indent=` in `Accept`) goes through DRF's renderer. For other data, the
output is identical to DRF's.

`MsgspecJSONParser` decodes with msgspec and reports malformed input as
DRF's `ParseError`. Unlike DRF's own `JSONParser` it is not declared pure,
so the body is parsed in the request's thread. It refuses what RFC 8259
does not allow and DRF's parser accepts: a number out of the float range
(`1e400`) and a lone surrogate escape (`"\ud800"`). Both are then a parse
error (`{"detail": "JSON parse error - ..."}`) where DRF's parser passes the
value on and the field answers (`{"a": ["A valid number is required."]}`);
a body with such a value in a field the serializer ignores is refused
whole.

## 4. Rolling out

Set `Meta.serializer_backend` on the serializers of list endpoints first and
run `aiodrf_inspect_serializers` to see which compile. Use
`serializer_backend_fallback = "error"` in tests for serializers that must
not fall back silently. `serializer_backend = "drf"` on a serializer is the
rollback. Compare responses before and after: in `"strict"` parity they must
be identical, in `"fast"` they differ where section 1 says.

## 5. Converting between serializers and schemas

`manage.py aiodrf_convert` writes source code for the other side, to start a
migration or to keep a schema next to a serializer:

```console
python manage.py aiodrf_convert app.serializers.BookSerializer --to pydantic
python manage.py aiodrf_convert app.serializers.BookSerializer --to msgspec --output app/schemas.py
python manage.py aiodrf_convert app.schemas.BookIn --to drf --name Book
```

A serializer is instantiated without a request and read from its `fields`;
a pydantic model or msgspec Struct is read from its class. Nested
serializers and models become classes of their own, written before the
class that uses them. A serializer with read-only or write-only fields
becomes a `BookIn` and a `BookOut` class, ready for `SchemaViewMixin`'s
`input_schema` and `output_schema`. The output is formatted as
`ruff format` would format it.

Field types and the options that have an equivalent are converted:
lengths, bounds, patterns, choices, `allow_null`, constant defaults,
`required=False` (`T | None = None` for pydantic, with a comment, and
`msgspec.UNSET` for msgspec), wire names (`source=`). Everything else is
kept visible, not guessed: the field becomes `Any` (or
`serializers.JSONField()`) with a `# TODO(aiodrf_convert): ...` comment.
The same comment lists `validate_<field>()`, `validate()`, every validator
a field or serializer carries beyond what its options build (explicit
`validators=`, a model's validators, the unique-together validators
`ModelSerializer` derives), pydantic validators and other `Annotated`
metadata that is not a constraint (`AfterValidator`, `Strict`,
`msgspec.Meta(tz=...)`), and `__post_init__`, which are code and are not
converted. A blank-able `CharField` accepts `""` whatever its `pattern`
(the pattern gains `^$|`) or `min_length` (noted, not converted). A field
pydantic would treat as private (`_id`) is renamed (`id_`) with its name as
the alias.

The generated classes accept and reject the same canonical JSON input as
the original. The libraries still
coerce differently: DRF accepts `"12"` for an integer and strips
whitespace from `CharField`s; strict msgspec does neither. Read the output
before using it.
