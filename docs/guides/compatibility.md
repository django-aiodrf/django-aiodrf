# Compatibility with DRF

aiodrf aims to behave exactly like DRF at every supported extension point. This
page explains how that is verified and lists the known differences. It does
not claim that every DRF subclass works unchanged: test your own extensions.

## Verification

- **DRF's own test suite** runs against aiodrf, with DRF's `APIView`, generic
  views, mixins, viewsets and `@api_view` replaced by aiodrf's, for DRF 3.16,
  3.17 and 3.18. Serializers, fields, permissions and renderers stay DRF's.
  DRF's tests call views synchronously, so each view is wrapped in
  `async_to_sync`, as Django's WSGI handler does with an async view.
- **Side-by-side comparison**: the same requests are sent to DRF's
  `ModelViewSet` and to aiodrf's, with both aiodrf and DRF serializers, and the
  status, headers, body and resulting database state are compared. The
  requests include filtering, search and ordering, invalid values of every
  field type, uniqueness violations, 404 and 405 responses, `OPTIONS`, server
  errors and every DRF paginator, with aiodrf's default settings and with the
  compiled serializer backends.
- **DRF upgrades**: aiodrf records every DRF function whose steps it repeats in
  its async code, so that a DRF release changing one of them is detected before
  it is supported. The private Django and DRF names aiodrf relies on are listed
  in [upstream internals](../reference/upstream-internals.md).
- **Upcoming releases**: the development versions of Django and DRF are tested
  weekly to detect changes early.

## Known differences

These DRF tests fail with aiodrf's views, by design:

| DRF behaviour | aiodrf behaviour |
| --- | --- |
| Generic views' handlers (`view.post(...)`) can be called directly from synchronous code. | aiodrf's handlers are coroutine functions; calling one from synchronous code returns a coroutine, as for any async Django view. |
| A viewset may override `dispatch()` to return a `Response`. | aiodrf awaits `dispatch()`, so an override must return an awaitable, for example `super().dispatch(...)`. |
| Every name in `http_method_names` is an `http.HTTPMethod`. | aiodrf accepts the HTTP QUERY method until Django supports it ([django#37232](https://code.djangoproject.com/ticket/37232)). |
| `ATOMIC_REQUESTS` rolls back a request that raised an API exception. | Django refuses `ATOMIC_REQUESTS` for async views (system check `aiodrf.W002`), so there is no request-wide transaction; see [transactions](migration-from-drf.md#5-transactions). |

With `AIODRF["ATOMIC_SAVE"]` enabled, which is the default, each save runs in a
transaction, so tests that count queries see an additional `SAVEPOINT` and
`RELEASE` around each save.
