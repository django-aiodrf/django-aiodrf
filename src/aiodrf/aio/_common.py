"""Bridge fallbacks shared by validation, representation and saving."""

from collections.abc import Callable
from typing import Any

from aiodrf.utils import (
    Impl,
    invoke,
    is_bridge_base,
    is_framework_class,
    resolve_pair,
)

#: Returned by the synchronous halves in _validate, _represent and _save for members
#: that must be awaited, which only the event loop can do.
NEEDS_AWAIT = object()


def _bridged(cls: type, sync_name: str) -> bool:
    """
    Return True if ``super()`` calls from ``cls`` reach aiodrf's synchronous
    bridge for ``sync_name``, which awaits the async members; DRF's own method
    would not.

    The project's overrides are passed over (they reach the next one through
    ``super()``); a framework class defining ``sync_name`` before the bridge
    (``class S(drf.Serializer, AsyncSerializerMixin)``) is what runs.

    Called by ``aio._validate`` and ``aio._save`` before invoking a sync hook.
    """
    for klass in cls.__mro__:
        if sync_name not in vars(klass):
            continue
        if vars(klass).get("_async_serializer_bridge", False):
            return True
        if is_framework_class(klass):
            return False
    return False


def _sync_member(
    serializer: Any, sync_name: str, async_name: str
) -> Callable[..., Any]:
    """
    The synchronous implementation a *default* may fall back on.

    ``serializer.<sync_name>`` would not do: on aiodrf serializers that is
    the sync bridge, which sends a user's async override straight back into
    the default it called through ``super()``. aiodrf's bridges are skipped,
    and so is a user's sync member when the async one is the implementation
    of the pair.

    Used by the default operations in ``aio._validate``, ``aio._represent``
    and ``aio._save``; it is not a public serializer extension point.
    """
    cls = type(serializer)
    async_wins = resolve_pair(serializer, sync_name, async_name) in (
        Impl.ASYNC,
        Impl.SYNC_IS_ASYNC,
    )
    if not async_wins and sync_name in vars(serializer):
        return getattr(serializer, sync_name)
    for klass in cls.__mro__:
        if sync_name not in klass.__dict__:
            continue
        if vars(klass).get("_async_serializer_bridge", False):
            continue
        if async_wins and not is_bridge_base(klass):
            continue
        return klass.__dict__[sync_name].__get__(serializer, cls)
    raise AttributeError(f"{cls.__qualname__} has no synchronous {sync_name}()")


# Local spelling in the validation and representation walkers; see utils.invoke.
_acall = invoke
