# Hook bridges and override resolution

`bridge_base` and `bridges_to` are private aiodrf implementation helpers.
They preserve synchronous DRF extension contracts while exposing awaitable hooks.
They are not Django decorators or application configuration APIs.

## Registration of framework defaults

`bridge_base(cls)` registers the exact class in `utils._BRIDGE_BASES`, a weak
set, and returns the same class. It does not replace methods, modify bases or
patch DRF. Registration invalidates pair-resolution metadata that may have
previously treated the class as an application override.

Identity matters: registering a base does not register all its subclasses.
A subclass implementing a hook remains custom. Framework modules register their
defaults at import time; this is not a request-time or concurrent configuration
API. Weak registrations do not retain dynamically constructed classes.

The loops registering DRF serializer and mixin bases identify existing defaults;
they do not alter those classes. Deleting the loop variable only removes a
temporary module name.

## Selecting a hook

`resolve_pair(obj, sync_name, async_name)` decides which implementation wins.
Execution context is a separate decision.

| Definition | Selection |
| --- | --- |
| Async member on the instance | Async member |
| Only sync member on the instance | Sync member, or coroutine under the sync name |
| Two custom class members | Closest defining class in the MRO; async wins a tie |
| Only one custom class member | That member |
| Only registered defaults | Framework default |

Pass the instance when one exists; inspecting only its type would miss an
instance-level override. Slotted objects without an instance dictionary fall
back to class inspection. The class-only decision is weakly cached; changing
class definitions during active requests is not a supported invalidation model.

This precedence is security-relevant. For example, accidentally selecting
`BasePermission.has_permission()` instead of an application's async denial
would allow access. The bridge tests cover overrides and inherited defaults,
not just successful view execution.

## Synchronous callers

`bridges_to("ahas_permission")` decorates the synchronous method on a framework
base. When the async override wins, the wrapper invokes it through
`async_to_sync(invoke)`; otherwise the original synchronous body runs.
This supports DRF code such as synchronous extension hooks, schema tooling and
the browsable API.

A bridge must run from synchronous code or a worker thread. It cannot block the
event-loop thread waiting for that same loop; async callers must await the async
operation. `call_pair_sync` additionally handles coroutine callables stored under
a synchronous name and synchronous wrappers returning awaitables.

`invoke` awaits known async callables. An unknown synchronous wrapper executes
in the thread-sensitive worker, then its returned awaitable is awaited on the
loop. A wrapper's `__wrapped__` metadata does not prove its synchronous prefix
is nonblocking.

## Serializer fallback and recursion

Async serializer defaults sometimes need DRF's original synchronous operation.
Calling the ordinary bound method could re-enter the reverse bridge indefinitely.
`aio._common._sync_member` therefore skips bases marked
`_async_serializer_bridge`. When async wins, it also skips custom sync methods
that lost the pair decision; when an instance sync override wins, it returns
that callable.

`_bridged(cls, sync_name)` checks both the marker and the requested method's
presence. An unrelated marked base must not satisfy a save or validation guard.
The marker identifies a fallback boundary; exact bridge-base registration
identifies framework defaults. Neither marker is a purity or thread-safety claim.

## State and cancellation

Class metadata caches have weak keys and capacity bounds. Computation occurs
outside publication locks; invalidation rejects stale publication. See
[state ownership](state-ownership.md) for cache lifetimes and retained values.

If a synchronous wrapper returns a native coroutine after its awaiting caller
has been cancelled, `run_sync_and_await` closes that coroutine. It cannot stop
an already running synchronous operation or roll back its side effects.
Applications still own their tasks, transactions and external resources.

## Source

- [Execution and registration helpers](../../src/aiodrf/utils.py).
- [Serializer fallback helpers](../../src/aiodrf/aio/_common.py).

These helpers are private. Extend applications through the
[documented hooks](../guides/extension-hooks.md); do not register arbitrary classes
as defaults to bypass execution checks.
