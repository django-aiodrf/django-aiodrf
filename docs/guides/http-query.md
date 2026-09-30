# HTTP QUERY

aiodrf provides explicit compatibility support for `QUERY`: an idempotent,
read-only request with selection criteria in its body. Support is scoped to
aiodrf views, routing, parsing and test clients; Django is not monkeypatched.

## Application handler

```python
from aiodrf import serializers
from aiodrf.response import Response
from aiodrf.views import APIView


class SearchInput(serializers.Serializer):
    term = serializers.CharField(max_length=40)


class SearchView(APIView):
    async def query(self, request):
        criteria = SearchInput(data=await request.adata())
        await criteria.ais_valid(raise_exception=True)
        return Response({"term": criteria.validated_data["term"]})
```

```console
curl -X QUERY http://127.0.0.1:8108/search/ \
  -H 'Content-Type: application/json' -d '{"term":"Ada"}'
```

Validation errors and unsupported media types retain DRF handling. Keep the
operation read-only: clients can retry it. Configure body-size limits,
authentication, row visibility and throttling as for other API methods.

## Permissions, CSRF and caching

Third-party code that hardcodes DRF's `SAFE_METHODS` may omit QUERY. Test the
actual permission and middleware stack. Session-authenticated requests may
require CSRF tokens under the underlying Django contract; the compatibility
layer does not disable CSRF globally.

Two QUERY requests to one URL can have different bodies. Ordinary URL-only
caches must not merge their results. `aiodrf.cache.cache_page` retains Django's
GET/HEAD caching policy and does not cache QUERY. A custom body-query cache must
include body identity and correct principal isolation, `Vary`, freshness and
invalidation. Validate ingress/proxy method acceptance and cache keying separately.

## OpenAPI and generated clients

OpenAPI 3.0/3.1 Path Item objects do not define a `query` operation. Configure
`aiodrf.contrib.spectacular.hooks.preprocess_exclude_query_method` to exclude
QUERY from these schemas. It does not convert QUERY into another method.
Generated clients therefore do not expose it. See the
[OpenAPI specification](https://spec.openapis.org/oas/v3.1.1.html#path-item-object).

Offer an explicitly documented POST endpoint for schema-generated clients.
It can delegate the search operation, but keeps POST's own CSRF/cache behavior.
The [policies example](../../examples/policies/README.md) includes both methods,
schema validation and Swagger UI.

## Transition to Django APIs

In tests, send QUERY requests with the `query()` method of `AsyncAPIClient` and
`AsyncAPIRequestFactory`.

aiodrf adds QUERY only when Django does not dispatch it itself. Once Django
supports the method, aiodrf will use Django's implementation, and its own
compatibility code will be removed when the oldest supported Django version
includes it.
