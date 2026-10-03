# Roadmap

This page lists planned work. The guides and reference pages describe what the
current release does and its limits.

## Native schemas

Pydantic and msgspec schemas can already serve as serializers, with
request-local Pydantic context, custom msgspec hooks and root or array output.
Planned work extends the tested data types to more combinations: nested and
recursive models, unions and discriminators, aliases, defaults and factories,
nullable and omitted values, constraints, bytes and durations, custom types,
Pydantic validators and configuration, and msgspec `Struct` options. Every
combination will be tested for partial updates, errors, schema generation and
round trips, and anything unsupported will be reported explicitly.

## Django's async support

aiodrf will adopt Django's native async ORM, connection and transaction APIs as
supported Django releases provide them.

- The compatibility code for the HTTP QUERY method will be removed once the
  oldest supported Django dispatches QUERY itself.
- Django 6.1 does not implement the ASGI lifespan protocol (ticket #31508 was
  closed as wontfix; [new-features#132](https://github.com/django/new-features/issues/132)
  proposes lifespan hooks). If Django adds one, `LifespanApplication` will
  delegate to it and `get_lifespan_state()` will remain the typed accessor.
- The optional [native PostgreSQL adapter](guides/async-backend.md) will follow
  the evolution of Django's backend APIs.

## Performance

- Continuous performance comparisons between releases, on dedicated hardware,
  following the [benchmark protocol](../benchmarks/README.md).
- Profiling of large responses and of the free-threaded build under contention.
- Guidance for sizing database connection pools across workers and replicas.

## Package ownership

The source packages under `forks/` have separate metadata, test matrices and
wheel checks. `django-fastdrf` owns the compiled serializer backends, field-copy
plans, input recognition, schema serializers, converter and auto-prefetch.
Aiodrf keeps the async DRF integration and pins the compatible fastdrf minor line.
Installed-wheel tests exercise compiled output, delegated hooks and input errors
across that boundary. Private fastdrf imports remain a compatibility constraint;
this extraction does not add a facade over them.

| Component | Owner |
| --- | --- |
| Native Redis/Valkey backends, codecs, async cache middleware and cache resource context | `aiodrf-async-cache`, using Django's cache contract |
| ASGI lifespan protocol, typed resource access, signals and lifecycle test helper | `aiodrf-asgi-lifespan`, optional for aiodrf |
| Request thread reuse, management commands, cache policy classification and async view `cache_page` | aiodrf |
| OpenSearch document writer | `aiodrf.contrib.opensearch`; no separate package |
| WhiteNoise adapter | `aiodrf.contrib.whitenoise`; future deprecation, with ServeStatic recommended for new deployments |
| Tracing, authentication, filters, permissions and schema adapters | aiodrf |
| Native ORM adapter, batch enrichment, concurrent representation, ADRF compatibility | aiodrf |

The extracted packages do not depend on DRF or aiodrf. Native cache backends
also work without lifespan; the default page-cache resource selector optionally
uses `aiodrf-asgi-lifespan`. Keep backend configuration in `CACHES` and the
resource factory in the top-level `DJANGO_LIFESPAN` setting. Old import paths
are removed rather than retained as compatibility aliases.

The cache package retains its integer-compatible msgspec encoding. Fastdrf's
codecs keep their own wire format; extraction does not make stored values
interchangeable. OpenSearch and WhiteNoise are not release dependencies of
either new package.

## Scope

New features address a demonstrated application need through Django's and DRF's
extension points. Replacing Django's ORM, URL routing, middleware or task API is
not a goal.
