"""
Evaluate DRF policy objects from async code.

These helpers accept any DRF authentication, permission, throttle or filter
backend instance: DRF's own classes, third-party classes and aiodrf classes
that implement the async member of a pair (``aauthenticate``,
``ahas_permission``, ``ahas_object_permission``, ``aallow_request``,
``afilter_queryset``). Synchronous implementations run inline when they are
known to be pure and in a thread otherwise.
"""

import enum
from collections.abc import Iterator, Sequence
from typing import Any

from django.db.models import QuerySet
from rest_framework import permissions, throttling
from rest_framework.authentication import BaseAuthentication
from rest_framework.request import Request
from rest_framework.views import APIView

import aiodrf._builtins  # noqa: F401 -- registers DRF's pure policies
from aiodrf.authentication import (
    _CALL,
    _CREDENTIAL_CHECKS,
    _SESSION,
    _authentication_kind,
    asession_authenticate,
)
from aiodrf.cache import is_in_process_cache
from aiodrf.permissions import BasePermission
from aiodrf.throttling import (
    AnonFixedWindowRateThrottle,
    FixedWindowRateThrottle,
    ScopedFixedWindowRateThrottle,
    UserFixedWindowRateThrottle,
)
from aiodrf.utils import (
    Impl,
    call_pair,
    call_pair_sync,
    class_cache,
    definer,
    depends_on_classification,
    is_pure,
    resolve_pair,
    run_sync,
    uses_sync,
)

__all__ = [
    "Mode",
    "aallow_request",
    "aauthenticate",
    "afilter_queryset",
    "ahas_object_permission",
    "ahas_permission",
    "allow_request",
    "authenticate",
    "filter_queryset",
    "has_object_permission",
    "has_permission",
    "permissions_mode",
    "reads_user",
    "throttles_mode",
]


class Mode(enum.Enum):
    #: Every policy is synchronous and pure: evaluate inline.
    INLINE = "inline"
    #: Every policy is synchronous: evaluate all of them in one thread hop.
    THREAD = "thread"
    #: At least one policy is async: evaluate them one by one.
    ASYNC = "async"


# -- Authentication -----------------------------------------------------------


async def aauthenticate(authenticator: BaseAuthentication, request: Request) -> Any:
    state = getattr(authenticator, "__dict__", None)
    kind: type | str
    if state and ("authenticate" in state or "aauthenticate" in state):
        kind = _CALL  # a method of its own: nothing is known about it
    else:
        kind = _authentication_kind(type(authenticator))
    if kind is _SESSION:
        return await asession_authenticate(authenticator, request)  # type: ignore[arg-type]  # a SessionAuthentication, by _authentication_kind
    if isinstance(kind, type):
        check = _CREDENTIAL_CHECKS.get(kind)
        if check is not None and not check(authenticator, request):
            # No hop for a request that carries nothing for this class.
            return None
    return await call_pair(authenticator, "authenticate", "aauthenticate", request)


# -- Permissions --------------------------------------------------------------

_OPERATORS = (permissions.AND, permissions.OR, permissions.NOT)


def _operands(operator: Any) -> tuple[Any, Any]:
    """``(op1, op2)`` of DRF's ``AND``, ``OR`` (and ``NOT``, whose ``op2`` is
    None). DRF sets them in ``__init__``; the type stubs do not declare them."""
    return operator.op1, getattr(operator, "op2", None)


def _leaves(permission: Any) -> Iterator[Any]:
    if type(permission) in _OPERATORS:
        op1, op2 = _operands(permission)
        yield from _leaves(op1)
        if op2 is not None:
            yield from _leaves(op2)
    else:
        yield permission


def _permission_uses_sync(permission: Any, sync_name: str, async_name: str) -> bool:
    if uses_sync(permission, sync_name, async_name):
        return True
    return _base_permission_grants(type(permission), sync_name, async_name)


def _permission_class_uses_sync(cls: Any, sync_name: str, async_name: str) -> bool:
    """:func:`_permission_uses_sync` for instances of ``cls`` without state."""
    return uses_sync(cls, sync_name, async_name) or _base_permission_grants(
        cls, sync_name, async_name
    )


def _base_permission_grants(cls: Any, sync_name: str, async_name: str) -> bool:
    # aiodrf's BasePermission grants in both members of a pair, as DRF's
    # does: a subclass that overrides neither is asked through the sync one.
    return (
        resolve_pair(cls, sync_name, async_name) is Impl.BASE
        and definer(cls, async_name) is BasePermission
    )


def permissions_mode(perms: Sequence[Any], *pairs: tuple[str, str]) -> Mode:
    """
    Decide how to evaluate ``perms`` for the given ``(sync, async)`` method
    name pairs, looking through DRF's ``&``, ``|`` and ``~`` operators.
    """
    leaves = [leaf for permission in perms for leaf in _leaves(permission)]
    if not all(_permission_uses_sync(leaf, *pair) for leaf in leaves for pair in pairs):
        return Mode.ASYNC
    if all(is_pure(leaf, sync_name) for leaf in leaves for sync_name, _ in pairs):
        return Mode.INLINE
    return Mode.THREAD


# DRF permissions that never look at the request.
_IGNORES_USER = {permissions.AllowAny, permissions.BasePermission}


def reads_user(perms: Sequence[Any], *pairs: tuple[str, str]) -> bool:
    """
    Return False when no permission can read ``request.user``.

    DRF authenticates lazily, the first time ``request.user`` is read. A view
    that overrides ``perform_authentication`` to do nothing therefore never
    looks at the credentials of a request that only meets ``AllowAny``.
    """
    return any(
        _may_read_user(type(leaf), sync_name, async_name)
        # A method set on the permission itself may read anything.
        or not getattr(leaf, "__dict__", {}).keys().isdisjoint((sync_name, async_name))
        for permission in perms
        for leaf in _leaves(permission)
        for sync_name, async_name in pairs
    )


def _may_read_user(cls: Any, sync_name: str, async_name: str) -> bool:
    # A class may implement the pair's async member only, on DRF's base.
    if resolve_pair(cls, sync_name, async_name) in (Impl.ASYNC, Impl.SYNC_IS_ASYNC):
        return True
    return definer(cls, sync_name) not in _IGNORES_USER


async def ahas_permission(permission: Any, request: Request, view: APIView) -> bool:
    # Operators are evaluated in order with short-circuiting, like DRF's.
    if type(permission) in _OPERATORS:
        op1, op2 = _operands(permission)
        if type(permission) is permissions.AND:
            return await ahas_permission(op1, request, view) and await ahas_permission(
                op2, request, view
            )
        if type(permission) is permissions.OR:
            return await ahas_permission(op1, request, view) or await ahas_permission(
                op2, request, view
            )
        return not await ahas_permission(op1, request, view)
    return await call_pair(
        permission, "has_permission", "ahas_permission", request, view
    )


async def ahas_object_permission(
    permission: Any, request: Request, view: APIView, obj: Any
) -> bool:
    if type(permission) in _OPERATORS:
        op1, op2 = _operands(permission)
        if type(permission) is permissions.AND:
            return await ahas_object_permission(
                op1, request, view, obj
            ) and await ahas_object_permission(op2, request, view, obj)
        if type(permission) is permissions.OR:
            # DRF re-checks ``has_permission`` for each operand so that
            # ``A | B`` cannot grant object access through the operand that
            # denied the request.
            return (
                await ahas_permission(op1, request, view)
                and await ahas_object_permission(op1, request, view, obj)
            ) or (
                await ahas_permission(op2, request, view)
                and await ahas_object_permission(op2, request, view, obj)
            )
        return not await ahas_object_permission(op1, request, view, obj)
    return await call_pair(
        permission,
        "has_object_permission",
        "ahas_object_permission",
        request,
        view,
        obj,
    )


# The synchronous twins, for the places where DRF checks synchronously. DRF's
# operators call ``op1.has_permission()`` directly, which for a leaf that
# only implements the async member is DRF's permissive default, so they are
# evaluated here as well.


def has_permission(permission: Any, request: Request, view: APIView) -> bool:
    # The operands DRF's operators keep; its stubs do not declare them.
    op1, op2 = getattr(permission, "op1", None), getattr(permission, "op2", None)
    if type(permission) is permissions.AND:
        return has_permission(op1, request, view) and has_permission(op2, request, view)
    if type(permission) is permissions.OR:
        return has_permission(op1, request, view) or has_permission(op2, request, view)
    if type(permission) is permissions.NOT:
        return not has_permission(op1, request, view)
    return call_pair_sync(
        permission, "has_permission", "ahas_permission", request, view
    )


def has_object_permission(
    permission: Any, request: Request, view: APIView, obj: Any
) -> bool:
    op1, op2 = getattr(permission, "op1", None), getattr(permission, "op2", None)
    if type(permission) is permissions.AND:
        return has_object_permission(op1, request, view, obj) and (
            has_object_permission(op2, request, view, obj)
        )
    if type(permission) is permissions.OR:
        return (
            has_permission(op1, request, view)
            and has_object_permission(op1, request, view, obj)
        ) or (
            has_permission(op2, request, view)
            and has_object_permission(op2, request, view, obj)
        )
    if type(permission) is permissions.NOT:
        return not has_object_permission(op1, request, view, obj)
    return call_pair_sync(
        permission,
        "has_object_permission",
        "ahas_object_permission",
        request,
        view,
        obj,
    )


# -- Throttles ----------------------------------------------------------------

_DRF_RATE_THROTTLES = {
    throttling.SimpleRateThrottle,
    throttling.AnonRateThrottle,
    throttling.UserRateThrottle,
    throttling.ScopedRateThrottle,
}
_FIXED_WINDOW_THROTTLES = {
    FixedWindowRateThrottle,
    AnonFixedWindowRateThrottle,
    UserFixedWindowRateThrottle,
    ScopedFixedWindowRateThrottle,
}
_RATE_THROTTLES = _DRF_RATE_THROTTLES | _FIXED_WINDOW_THROTTLES
# What a rate throttle's allow_request() reaches (get_ident() from the key,
# get_rate()/parse_rate() for a scoped one), and wait(), which the view calls
# after a denial. DRF's BaseThrottle defines get_ident().
_RATE_THROTTLE_HOOKS = (
    "allow_request",
    "get_cache_key",
    "get_ident",
    "get_rate",
    "parse_rate",
    "throttle_success",
    "throttle_failure",
    "timer",
    "wait",
)
_RATE_THROTTLE_DEFINERS = _RATE_THROTTLES | {throttling.BaseThrottle}


@depends_on_classification
@class_cache
def _has_framework_rate_hooks(cls: Any) -> bool:
    """Whether every hook of the rate throttles ``cls`` has is DRF's or aiodrf's."""
    return all(
        definer(cls, name) in _RATE_THROTTLE_DEFINERS for name in _RATE_THROTTLE_HOOKS
    )


def _is_pure_throttle(throttle: Any) -> bool:
    if is_pure(throttle, "allow_request"):
        return True
    # The rate throttles only do I/O through their cache. With an in-process
    # cache they can run on the event loop, unless the project overrides a
    # hook they reach: allow_request() calls the others, and the view calls
    # wait() after a denial.
    cls = type(throttle)
    if not _has_framework_rate_hooks(cls) or not getattr(
        throttle, "__dict__", {}
    ).keys().isdisjoint(_RATE_THROTTLE_HOOKS):
        # A hook of the project's, on the class or set on the throttle.
        return False
    # A scoped fixed-window throttle takes allow_request() from DRF's scoped
    # one, which reaches the fixed window's add() and incr() through super().
    if issubclass(cls, FixedWindowRateThrottle):
        return is_in_process_cache(throttle.cache, methods=("add", "incr"))
    return is_in_process_cache(throttle.cache)


def throttles_mode(throttles: Sequence[throttling.BaseThrottle]) -> Mode:
    if not all(uses_sync(t, "allow_request", "aallow_request") for t in throttles):
        return Mode.ASYNC
    if all(_is_pure_throttle(t) for t in throttles):
        return Mode.INLINE
    return Mode.THREAD


async def aallow_request(
    throttle: throttling.BaseThrottle, request: Request, view: APIView
) -> bool:
    if uses_sync(throttle, "allow_request", "aallow_request") and _is_pure_throttle(
        throttle
    ):
        return throttle.allow_request(request, view)
    return await call_pair(throttle, "allow_request", "aallow_request", request, view)


def allow_request(
    throttle: throttling.BaseThrottle, request: Request, view: APIView
) -> bool:
    return call_pair_sync(throttle, "allow_request", "aallow_request", request, view)


async def _throttle_wait(throttle: throttling.BaseThrottle) -> float | None:
    """
    ``throttle.wait()`` for a throttle that refused the request: inline when
    ``wait`` is pure, in one hop otherwise. Where ``allow_request()`` ran
    says nothing of ``wait()``.

    Called by ``views.APIView.acheck_throttles`` and the ADRF compatibility
    view adapter, which use the same waiting-time policy.
    """
    if is_pure(throttle, "wait"):
        return throttle.wait()
    return await run_sync(throttle.wait)()


def authenticate(authenticator: BaseAuthentication, request: Request) -> Any:
    return call_pair_sync(authenticator, "authenticate", "aauthenticate", request)


# -- Filter backends ----------------------------------------------------------


def filter_queryset(
    backend: Any, request: Request, queryset: QuerySet[Any], view: APIView
) -> QuerySet[Any]:
    return call_pair_sync(
        backend, "filter_queryset", "afilter_queryset", request, queryset, view
    )


async def afilter_queryset(
    backend: Any, request: Request, queryset: QuerySet[Any], view: APIView
) -> QuerySet[Any]:
    return await call_pair(
        backend, "filter_queryset", "afilter_queryset", request, queryset, view
    )
