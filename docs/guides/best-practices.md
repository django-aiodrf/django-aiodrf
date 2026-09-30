# Application best practices

## Preserve Django's ownership boundaries

Use the ORM, database routers, middleware, storage and cache APIs provided by
Django. Configure vendor packages through their documented settings. Core
aiodrf integration must not depend on monkeypatching Django or DRF. Existing
ADRF import compatibility, experimental middleware subclasses and the external
native database adapter are explicit exceptions with separate documentation;
they are not prerequisites for using aiodrf.

Keep authorization in authentication/permission hooks, validation in serializers
and persistence in save hooks or application services. Avoid duplicating DRF
dispatch, error formatting or serializer binding to implement a local policy.
Use `super()` when extending a hook whose inherited behavior is required.

## Use async for awaited I/O

Use async HTTP clients with bounded connection pools and explicit timeouts.
Create loop-bound resources in lifespan and close them at shutdown. Keep
database transactions and synchronous vendor operations in a single worker
unit. Do not mark an operation `async_safe` merely because it is usually fast.

Django's async ORM entry points ordinarily adapt the synchronous database
implementation. They do not select a native async driver. Batch related reads
and use `select_related()`/`prefetch_related()` before increasing concurrency.
Paginate large results and bound per-item external calls.

## Keep request state isolated

Do not store requests, users, serializer instances or event-loop resources in
mutable module globals. Configuration declarations and immutable registries
are different from request state. Use serializer context for request-local
data and typed lifespan state for application resources. A serializer instance
is not a concurrent work queue.

## Retain security checks

Keep session CSRF protection, explicit object permissions and request-body
limits. Cache only responses whose variation and authorization rules are
understood; include the relevant tenant/user/language identity in keys where
required. Do not cache an authenticated response under a public URL-only key.

Do not log credentials, request bodies or arbitrary exception attributes by
default. Configure trace sampling and exporters in the application. File
storage and task queues require application authorization and retention rules.
Example development credentials and open endpoints are not deployment defaults.

## Maintain observable contracts

Document serializer backend selection and any fast-parity differences. Prefer
strict parity until measurements justify another mode. Pin response/error
contracts in tests before tuning. Profile representative payloads, queries and
concurrency; compare equivalent workloads on the same runtime and hardware.

Follow the [testing guide](testing.md), [settings reference](../reference/settings.md)
and [contribution standards](../../CONTRIBUTING.md) when extending the framework.
