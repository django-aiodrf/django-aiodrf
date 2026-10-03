# Architecture and integration boundaries

django-aiodrf extends DRF classes and preserves their synchronous contracts.
Awaitable operations use explicit hooks and a shared override resolver. The
[implementation guide](../guides/implementation.md) describes dispatch and
serializer execution; [bridge resolution](bridge-pattern.md) describes inheritance.

## Subclass-based integration

DRF serializers, fields, routers, policy objects and exception types remain
the application's extension points. A view can migrate without replacing its
models or moving authentication to another framework. Unknown synchronous
extensions run in a thread-sensitive worker; supported coroutine hooks are
awaited. This preserves thread affinity without claiming that all dependencies
use native asynchronous I/O.

## Alternatives

| Approach | Appropriate use | Constraint |
| --- | --- | --- |
| Unchanged synchronous DRF | Endpoints dominated by synchronous work | Does not provide awaited serializer or policy hooks |
| Explicit subclasses | Incremental async endpoints and DRF extension reuse | Requires parity tests for mirrored lifecycle operations |
| Process-wide replacement | Exceptional, separately reviewed compatibility tools | Import order, ownership and rollback become process-wide concerns |
| Independent execution engine | Applications willing to change API contracts | Requires separate ecosystem adapters and migration |
| Upstream DRF integration | Extension accepted into DRF itself | Must retain existing synchronous APIs; optional vendors belong outside core |

Core dispatch does not monkeypatch Django or DRF. Optional serializer
optimizations come from django-fastdrf (configured with `FASTDRF`); aiodrf's own
opt-ins (`list_prefetch`, `concurrent`) live under `aiodrf.contrib.builtin`;
vendor integrations remain separately selected.
See the [runtime adaptation inventory](../reference/runtime-adaptations.md) for
the limited compatibility mechanisms that change runtime state.

## Maintenance obligations

Compare outputs, validation errors, hook order, query behavior, cancellation
and resource ownership against the supported Django/DRF versions. Keep defaults
conservative and publish unsupported semantics in the
[limitations reference](../reference/limitations.md). Compiler eligibility is not
an assertion that arbitrary application code is pure or thread-safe.
