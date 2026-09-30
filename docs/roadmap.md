# Roadmap

This page lists planned work. The guides and reference pages describe what the
current release does and its limits.

## First release

The first release, 0.0.1, will be published on PyPI together with the
documentation site. Until then, install the package from a checkout of the
repository as described in the [README](../README.md).

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

## Separate packages

Some contrib modules could become packages of their own if they gain users
outside aiodrf. None is planned yet; each would first need a stable API, its own
test matrix and a migration path for existing imports.

| Component | Possible direction |
| --- | --- |
| Native Redis and Valkey cache backends, cache codecs, page-cache middleware | A general async cache package for Django; its API does not depend on DRF |
| OpenSearch document writer | A contribution to `django-opensearch-dsl` |
| WhiteNoise async adapter | A contribution to WhiteNoise |
| Compiled serializer backends and field-copy plans | A DRF-focused package usable by synchronous and asynchronous code |
| Pydantic and msgspec serializers and converters | A DRF adapter package, once the schema-native and DRF-compatible parts are separated |
| Tracing, authentication, filter, permission and schema adapters | Remain in aiodrf: they exist to integrate with aiodrf's hooks |
| Native ORM backend, query prefetching, concurrent representation | Remain in aiodrf until Django's async ORM APIs are stable |
| ADRF import compatibility | Remains a migration aid in aiodrf |

## Scope

New features address a demonstrated application need through Django's and DRF's
extension points. Replacing Django's ORM, URL routing, middleware or task API is
not a goal.
