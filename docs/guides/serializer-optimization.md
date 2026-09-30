# Selective serializer optimization

Enable one optimization on a measured endpoint, retaining a DRF reference
contract. Request-scoped selection must not mutate Django settings, serializer
classes or global registries.

## Scope and precedence

```python
from aiodrf import serializers, viewsets


class ProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = ["id", "name", "price"]
        cache_fields = True
        field_copy_mode = "compiled"


class ProductViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Product.objects.all()
    serializer_class = ProductSerializer
    serializer_field_cache = True
    serializer_field_copy_mode = "compiled"
```

The view attributes are an alternative to `Meta`, not a requirement to repeat
it. Resolution is nearest serializer `Meta`, parent serializer `Meta`, view
attributes from DRF's ordinary serializer context, then global `AIODRF`
settings. `None` inherits; `cache_fields=False` explicitly disables. A nested
serializer can opt out even when its parent/view opts in. Without a view in
context, standalone serializers use Meta/global settings. Custom factories
that omit DRF's context intentionally do not receive view-scoped options. The
view is read from a `dict` context, subclasses included, without calling the
context's own methods; a context of another mapping type uses Meta/global
settings.

## Copy strategies

| Mode | Implementation | Boundary |
| --- | --- | --- |
| `deepcopy` | DRF copies each cached field | Default; no constructor bypass |
| `clone` | Prepared scalar state copies | Exact supported scalar classes only |
| `compiled` | Recursive constructor-argument execution plan | Scalar clones plus nested/container planning; custom copy/queryset behavior retained |

All require field caching to be enabled. Model field caching retains the static
class eligibility checks. Plain aiodrf Serializer declarations also use the
recursive plan in compiled mode. Nested ordinary DRF serializers remain valid,
but their own `get_fields()` is not rewritten. Custom constructors still run;
the feature does not promise to accelerate every leaf. No global Field or
Serializer method is patched, and no dynamic Python source is generated.

Mutable styles, defaults and binding state belong to each copied field tree.
Validators explicitly supplied to DRF preserve DRF's sharing semantics.
Querysets remain lazy and independent. Unsupported/custom copying uses DRF
rather than approximating it. Runtime mutation of model/Meta/declared-field
definitions is outside the cache contract: disable it for those serializers.

## Compiled values are a separate choice

`SERIALIZER_BACKEND`/`Meta.serializer_backend` selects msgspec or Pydantic
validation recognition/output compilation, or the dependency-free python
backend's output compilation. It is independent of copying.
Start with strict parity; do not enable fast parity globally to silence an
eligibility failure. A Decimal-heavy endpoint can benefit from field caching
while retaining DRF output. The [backend guide](msgspec-pydantic.md) explains
supported types and fallback.

## Verification before deployment

Test valid/invalid input, PATCH, nested errors, locale changes, independent
request context, duplicate relations, custom defaults and serializer reuse
rules. Compare rendered bytes and error codes with ordinary DRF. Include schema
generation and the browsable API if used. Measure warm/cold construction,
database query count, concurrent latency and memory: faster field copying alone
does not make an endpoint faster. Keep the default profile ready to switch back
to, and repeat these checks after upgrading Django or DRF.
