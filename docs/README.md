# django-aiodrf documentation

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../assets/aiodrf-logo/svg/aiodrf-logo-dark-bg.svg">
  <img src="../assets/aiodrf-logo/svg/aiodrf-logo.svg" alt="django-aiodrf" width="420" height="93">
</picture>

aiodrf adds awaitable views, serializer operations and policy hooks to Django
REST framework. Existing DRF components keep working. Synchronous code that
aiodrf cannot prove free of I/O runs in a worker thread, so an async view never
blocks the event loop on it; an async view does not, however, make a
synchronous dependency such as the database driver asynchronous.

## Getting started

1. Install the package and write a first view: see the [README](../README.md).
2. Run the [basics](../examples/basics/README.md) or
   [CRUD and filtering](../examples/crud/README.md) example.
3. When adapting an existing project, follow the
   [DRF integration guide](guides/drf-integration.md).
4. Test your API with the [testing guide](guides/testing.md).

## Reference

- [API reference](api-reference.md): views, serializers, hooks and optional modules.
- [Settings](reference/settings.md): defaults, accepted values and when each
  setting takes effect.
- [System checks](reference/checks.md): every check, its cause and how to resolve it.
- [Limitations](reference/limitations.md): what aiodrf does not do, and where
  its guarantees end.
- [Extension hooks](guides/extension-hooks.md): sync/async pairs, inheritance,
  exceptions, conditional requests and query validation.
- [HTTP QUERY](guides/http-query.md): request bodies for read queries.
- [Serializer data types](reference/serializer-type-coverage.md).
- [Runtime adaptations](reference/runtime-adaptations.md) and
  [upstream internals](reference/upstream-internals.md): what aiodrf changes in
  Django and DRF when asked to, and which of their private names it relies on.
- [Versioning and compatibility](guides/releasing.md).

## Building applications

- [Best practices](guides/best-practices.md).
- [Transactions in async views](guides/async-transactions.md) and
  [Django fetch modes](guides/fetch-modes.md).
- [Lifespan](guides/lifespan.md), [management commands](guides/management-commands.md)
  and [Django Tasks](guides/tasks.md).
- [Streaming responses](guides/streaming-schema.md) and
  [static files under ASGI](guides/static-files.md).
- [Batch enrichment](guides/prefetch.md) and
  [concurrent representation](guides/concurrent-serialization.md).
- [Native async cache](guides/async-cache.md),
  [cache failure handling](guides/cache-resilience.md) and
  [NoSQL databases](guides/async-nosql.md).
- [Native PostgreSQL backend](guides/async-backend.md).
- [Independent ASGI endpoints](architecture/asgi-composition.md).
- [Examples](../examples/README.md).

## Integrations

- [Tested third-party packages](guides/ecosystem.md) and their
  [recorded versions](reference/ecosystem-versions.md).
- [Optional dependencies](reference/contrib-dependencies.md).
- [Compatibility with DRF's own test suite](guides/compatibility.md).

## Performance

- [Tuning](guides/tuning.md) and [measuring performance](guides/performance.md).
- [Serializer backends](guides/msgspec-pydantic.md) and
  [selective serializer optimization](guides/serializer-optimization.md).
- [The tuned profile](guides/tuned-profile.md): each opt-in, its effect and its risks.
- [CPU-bound work](guides/cpu-work.md).

## Deployment

- [Deployment](guides/deployment.md) and [ASGI servers](guides/web-servers.md).
- [Streaming behind proxies](guides/stream-proxies.md).
- [Validating a deployment](guides/deployment-validation.md).

## Migration

- [From DRF](guides/migration-from-drf.md).
- [From ADRF](guides/migration-from-adrf.md) and a
  [comparison of the two designs](architecture/adrf-comparison.md).
- [Experimental middleware scheduling](guides/unsafe-middleware.md).

## Design

- [Framework design](framework_design.md) and the
  [implementation reference](guides/implementation.md).
- [Bridge pattern](architecture/bridge-pattern.md),
  [thread boundaries](architecture/thread-boundaries.md),
  [state ownership](architecture/state-ownership.md),
  [naming](architecture/naming.md) and
  [alternatives considered](architecture/alternatives.md).

## Project

- [Changelog](../CHANGELOG.md) and [roadmap](roadmap.md).
- [Contributing](../CONTRIBUTING.md), [code of conduct](../CODE_OF_CONDUCT.md)
  and [security policy](../SECURITY.md).
