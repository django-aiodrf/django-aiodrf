# Serializer data types

This page lists, for each data type, how the msgspec and Pydantic backends
handle it; the python backend compiles the same output as they do and leaves
input to DRF. Every case is tested with each backend: for DRF serializers, input
recognition and compiled output are tested separately, and compiled output is
tested without the possibility of falling back to DRF. For msgspec and Pydantic
schemas, validation, Python value types and JSON output are compared with the
library used directly.

The table covers DRF's concrete field types and representative msgspec and
Pydantic types. Custom Pydantic types, validators and annotation combinations
and msgspec extension protocols are open-ended, so not all of them are listed.
Abstract `Field` and `RelatedField` classes are not usable on their own, and
`ModelSerializer` and `HyperlinkedModelSerializer` build fields rather than add
data types.

## DRF input

These results apply to both backends with canonical values. A compiled input
plan is a recognizer: anything it cannot prove equivalent is validated by DRF.
Invalid inputs keep DRF error messages and codes. Output's `fallback="error"`
policy does not disable that input behavior.

| DRF field | Input path | Reason or constraint |
| --- | --- | --- |
| BooleanField, IntegerField, BigIntegerField, FloatField | Recognized | Exact canonical types; numeric bounds and finite floats |
| CharField | Recognized | String, whitespace, null-character, surrogate and length rules |
| ChoiceField | Recognized | Non-colliding string/integer choices; no custom validator |
| UUIDField | Recognized | UUID objects and canonical text; no custom validator |
| DateField | Recognized | ISO input format; dates and accepted ISO strings |
| TimeField | Recognized | ISO input format, naive time; offset-bearing values use DRF |
| ListField, DictField, HStoreField | Recognized recursively | Supported child contract, nullability, emptiness and length bounds |
| Serializer, ListSerializer | Recognized recursively | Ordinary validation hooks, supported children and list options |
| EmailField, RegexField, SlugField, URLField, IPAddressField | DRF | Django/DRF validators and normalization are not vendor equivalents |
| DecimalField | DRF | Precision, quantization, rounding and localization |
| DateTimeField | DRF | Active timezone, naive/aware conversion and input formats |
| DurationField | DRF | Django's duration syntax and bounds are not vendor defaults |
| MultipleChoiceField, FilePathField | DRF | Set/choice conversion or filesystem-derived choices |
| FileField, ImageField | DRF | Upload objects, image inspection, size and filename rules |
| JSONField | DRF | Encoder options and non-JSON Python values require DRF validation |
| ModelField | DRF | Model field's conversion contract |
| PrimaryKeyRelatedField, SlugRelatedField, HyperlinkedRelatedField, ManyRelatedField | DRF | Queryset scope, object lookup and relationship validation |
| HiddenField | DRF | Defaults and request-dependent hidden values |
| ReadOnlyField, SerializerMethodField, StringRelatedField, HyperlinkedIdentityField | Not input | Ignored by validation; recognizing an empty writable set does not compile their output |

Untyped containers, subclasses, callable defaults, serializer validators and
custom sync/async hooks remain on DRF. Compilation never bypasses uniqueness
checks, querysets filtered for authorization or your own validation.

## Output

The table shows support per field type, as with a plain serializer in fast
parity; strict parity also requires the field to read a supported model field
and source. That a library supports a type does not mean its output matches
DRF's.

| Output family | Compiled behavior |
| --- | --- |
| Scalar strings, integers, floats, booleans, UUIDs, dates, times and matching choices | Strict compilation for matching model fields and supported formats |
| Datetime | DRF's output in either parity: the current time zone and ISO 8601, read once per output |
| Decimal output as a string | DRF's quantization, rounding and normalization in either parity; a `Decimal` output (not coerced to a string) compiles in fast parity only, as `str(value)` |
| Primary keys and slugs of forward, many-to-many and reverse relations; eligible nested model relations | Supported; relationship loading and query semantics remain Django's |
| Dotted sources through foreign keys that cannot be null | Supported; through a nullable key, a related manager or to a property, DRF |
| Values of the instance alone (annotations) read by a scalar field | Supported; an instance without the value is DRF's |
| File and image fields | Supported: the URL, absolute with the request in the context, or the name |
| `ModelField` (generated fields) of strings, numbers and booleans | Supported |
| JSON | Fast parity uses backend encoding; strict parity retains DRF's intermediate values |
| ListField, DictField, HStoreField, DurationField, MultipleChoiceField, other `ModelField`s | DRF representation; new input support does not imply output support |
| Methods, `source="*"`, custom representation and unsupported relation shapes | DRF representation; user code must not disappear |

See [backend configuration](../guides/msgspec-pydantic.md) for source checks,
dynamic field signatures, cache limits and explicit fallback errors. Fast parity
is not the default, and its output can differ from DRF's.

## Schema-native serializers

`MsgspecSerializer` and `PydanticSerializer` do not compile DRF fields. They
delegate to an explicitly supplied Struct or BaseModel and have no DRF input
fallback. They support null, booleans, numbers, strings, bytes,
lists, mappings, fixed/variable tuples, sets, frozen sets, date/time/datetime,
durations, UUID, decimal, enums, dataclasses, named tuples, TypedDict, Any,
optional/union/literal/new-type annotations, abstract sequences/mappings,
constraints and nested vendor schemas. Engine-specific cases cover bytearray
for msgspec and paths, IP addresses/networks, URLs, secrets and deque for Pydantic.

Their behaviour can differ from DRF's: bytes encoding, decimal formatting, enum
conversion and secret masking follow the library. Pydantic's `deque` is
accepted only with lenient (non-strict) input. Not every supported type has a
JSON Schema or OpenAPI representation. MessagePack extension values, arbitrary
Python objects, callables and custom hooks are not JSON data types; handle them
explicitly in your application.

For vendor-defined type semantics, consult the
[msgspec type catalogue](https://github.com/jcrist/msgspec/blob/main/docs/supported-types.rst)
and [Pydantic standard-library type reference](https://docs.pydantic.dev/latest/api/standard_library_types/).
Schema options, validators, partial updates, aliases and datetimes are
supported, as are request-scoped Pydantic validation and output context, extra
fields, `RootModel` and scalar or array output, msgspec's custom decode, encode
and schema hooks, and redaction in nested subclasses. The
[typed schemas example](../../examples/typed-schemas/README.md) shows them
through real ASGI requests and in the OpenAPI schema. Planned additions are
listed in the [roadmap](../roadmap.md#native-schemas).
