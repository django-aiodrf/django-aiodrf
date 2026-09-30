# Architectural comparison with ADRF

ADRF and aiodrf both extend Django REST framework for asynchronous applications.
Neither replaces Django's ORM or makes arbitrary synchronous application code
nonblocking. ADRF is an independent project, not an aiodrf dependency.

This comparison examines ADRF **0.1.14**, the version used by the migration
contract, and the current aiodrf source. It is a comparison of implementation
contracts, not a performance ranking or a claim about future ADRF releases.
The reference source is [ADRF](https://github.com/em1208/adrf), particularly
`views.py`, `mixins.py`, `serializers.py` and `routers.py` in that release.

## Execution and extension contracts

| Concern | ADRF 0.1.14 | aiodrf |
| --- | --- | --- |
| CRUD action names | Async generic actions such as `alist` and `acreate`; its router maps standard routes to those names | Async actions retain DRF names such as `list` and `create`; ordinary DRF routers are reused |
| Create/update hooks | `perform_acreate` and `perform_aupdate` | Sync `perform_create`/`perform_update` and explicit `aperform_create`/`aperform_update` pairs |
| Serializer validation | CRUD mixins offload synchronous `is_valid()` | Awaitable validation through `aio.is_valid()` or `ais_valid()`, including supported async custom validators |
| Serializer output | Awaitable `adata` and async representation methods; ordinary DRF output also has an adapter | `aio.data()` accepts DRF serializers; aiodrf bases expose `adata()` and reverse bridges for synchronous callers |
| Policy scheduling | Separates sync/async policy groups; async permission checks are gathered | Preserves declaration order and short-circuit behavior, with sync/async method-pair selection |
| Synchronous extension work | Explicit adaptations in the async request/serializer paths | Groups compatible synchronous work and resumes at explicit async boundaries |

ADRF already supports DRF components and asynchronous serializers. It would be
incorrect to describe it as only an async view decorator. Its smaller API surface
can be appropriate for an application already using its action names and
serializer conventions.

## Why aiodrf has a separate execution layer

aiodrf's distinguishing requirement is mixed extension code: existing sync DRF
overrides, new async hooks and shared classes used by both sync and async views.
Supporting that requirement is more than changing methods to `async def`.

Method-pair resolution follows the class hierarchy. The bridge metadata records
which synchronous entry points have async counterparts; it does not declare a
method safe to run on the event loop. Reverse bridges allow an aiodrf serializer
or policy to retain a synchronous interface when ordinary DRF calls it. Such a
call still blocks its caller's thread until the asynchronous work completes.
See [bridge semantics](bridge-pattern.md) and [extension hooks](../guides/extension-hooks.md).

CRUD and serializer orchestration group synchronous work where the selected
hooks permit it. A continuation resumes an operation when it encounters a genuine
async override without replaying completed hooks. Unknown code stays in a worker
by default. Exact built-in operations and explicit declarations can use inline
execution, subject to documented limits. This avoids treating third-party module
names or an async view declaration as proof that a call cannot block.

The trade-off is a larger maintenance surface: override precedence, descriptors,
`super()` calls, worker affinity and cancellation all need regression coverage.
The design is worthwhile only while that behaviour stays explicit and tested;
fewer bridge calls alone do not make an application faster.

## Features beyond async adaptation

aiodrf additionally provides opt-in serializer backends, field-template/copy
plans, relation-lookup batching, typed input adapters, streaming responses,
lifespan resource ownership and selected ecosystem integrations. These are
separate capabilities, not requirements for ordinary async DRF endpoints.

Compilation retains DRF declarations but has eligibility checks and fallbacks.
Fast parity and raw typed schemas have separate compatibility limits. Field-copy
plans cannot assume a custom field's constructor or deepcopy has no side effects.
Lifespan resources belong to each worker's loop, not process-global application
state. See the [framework design](../framework_design.md) and
[limitations](../reference/limitations.md).

These features increase dependency, documentation and testing obligations. Their
presence does not imply that an application needs them or that ADRF's API should
be expanded in the same direction.

## Compatibility and migration

Both packages retain Django/DRF dependencies and therefore inherit their limits:
ordinary Django ORM async entry points still adapt synchronous database work,
transactions need an explicitly owned synchronous unit, and synchronous middleware
or vendors may introduce thread transitions.

Use the [ADRF migration guide](../guides/migration-from-adrf.md) for naming and
behavior changes. aiodrf's codemod produces reviewable source edits. Its separate,
default-off ADRF import shim installs `sys.modules` aliases: it executes aiodrf
code, not ADRF, and is a documented runtime adaptation. It must not be described
as a no-patch equivalence layer. Prefer explicit imports for new application code.

A sample application has been converted from ADRF to aiodrf and compared
request by request, but that does not cover every ADRF extension or third-party
package. Check your own overrides and the order of your authorization checks
when migrating. A working ADRF application has no reason to migrate on the
basis of this comparison alone.
