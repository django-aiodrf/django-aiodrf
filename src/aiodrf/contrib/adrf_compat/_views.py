"""
adrf's view API on aiodrf's views.

A class that defines one of adrf's names gets aiodrf's name for it too, in
its own ``__dict__``, so aiodrf's code (the concrete views' ``get()``,
``call_pair``) finds it: ``alist`` becomes ``list``, ``perform_acreate``
becomes ``aperform_create``. The adrf names on the base classes call
aiodrf's implementation directly, so ``super().alist()`` does not come back
to the override.

``check_async_permissions`` and its siblings take the policies to check, as
in adrf. Overriding one of a group replaces aiodrf's check for that group
with adrf's: async policies (an ``async def`` member, or a DRF operator with
one) go to the ``check_async_*`` method, the others to ``check_sync_*`` in a
thread hop; permissions async first, throttles sync first, as adrf does.
"""

import inspect
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, NoReturn

from rest_framework.request import Request

from aiodrf import policies
from aiodrf.contrib.adrf_compat import warn_once
from aiodrf.policies import Mode, _throttle_wait
from aiodrf.utils import call_pair, run_sync, user_defines
from aiodrf.views import _constructs_inline

# adrf's name: aiodrf's.
_NAMES = {
    "alist": "list",
    "acreate": "create",
    "aretrieve": "retrieve",
    "aupdate": "update",
    "partial_aupdate": "partial_update",
    "adestroy": "destroy",
    "perform_acreate": "aperform_create",
    "perform_aupdate": "aperform_update",
    "perform_adestroy": "aperform_destroy",
    "get_apaginated_response": "aget_paginated_response",
}

_PERMISSION = ("has_permission", "ahas_permission")
_OBJECT_PERMISSION = ("has_object_permission", "ahas_object_permission")


def _awaits(policy: Any, pair: tuple[str, str]) -> bool:
    return policies.permissions_mode([policy], pair) is Mode.ASYNC


def _split(items: Iterable[Any], pair: tuple[str, str]) -> tuple[list[Any], list[Any]]:
    sync: list[Any] = []
    asynchronous: list[Any] = []
    for item in items:
        (asynchronous if _awaits(item, pair) else sync).append(item)
    return sync, asynchronous


async def _permissions(view: Any) -> Any:
    # As aiodrf's own check: a factory or constructor the project wrote may
    # query, and then builds the permissions in the worker.
    if user_defines(view, "get_permissions") or not _constructs_inline(
        *view.permission_classes
    ):
        return await run_sync(view.get_permissions)()
    return view.get_permissions()


async def _acheck_permissions(self: Any, request: Request) -> None:
    sync, asynchronous = _split(await _permissions(self), _PERMISSION)
    if asynchronous:
        await self.check_async_permissions(request, asynchronous)
    if sync:
        await run_sync(self.check_sync_permissions)(request, sync)


async def _acheck_object_permissions(self: Any, request: Request, obj: Any) -> None:
    sync, asynchronous = _split(await _permissions(self), _OBJECT_PERMISSION)
    if asynchronous:
        await self.check_async_object_permissions(request, asynchronous, obj)
    if sync:
        await run_sync(self.check_sync_object_permissions)(request, sync, obj)


async def _acheck_throttles(self: Any, request: Request) -> None:
    throttles = await run_sync(self.get_throttles)()
    sync = [t for t in throttles if not inspect.iscoroutinefunction(t.allow_request)]
    asynchronous = [
        t for t in throttles if inspect.iscoroutinefunction(t.allow_request)
    ]
    durations = []
    if sync:
        durations.extend(await run_sync(self.check_sync_throttles)(request, sync))
    if asynchronous:
        durations.extend(await self.check_async_throttles(request, asynchronous))
    if durations:
        # DRF's: ``None`` values come from changed configuration (DRF #1438).
        self.throttled(
            request, max((d for d in durations if d is not None), default=None)
        )


# adrf's methods that, when overridden, stand in for aiodrf's check.
_CHECKS = {
    ("check_async_permissions", "check_sync_permissions"): (
        "acheck_permissions",
        _acheck_permissions,
    ),
    ("check_async_object_permissions", "check_sync_object_permissions"): (
        "acheck_object_permissions",
        _acheck_object_permissions,
    ),
    ("check_async_throttles", "check_sync_throttles"): (
        "acheck_throttles",
        _acheck_throttles,
    ),
}


if TYPE_CHECKING:
    # The aiodrf view the mixins are mixed into, for the type checker only.
    from aiodrf.views import APIView as _View
else:
    _View = object


class AdrfViewMixin(_View):
    """
    adrf's ``APIView`` members, on an aiodrf view it is mixed into.
    """

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if cls.__module__.startswith(__package__ + "."):
            return
        own = vars(cls)
        for adrf_name, name in _NAMES.items():
            if adrf_name in own and name not in own:
                warn_once(adrf_name, name)
                setattr(cls, name, own[adrf_name])
        for group, (name, check) in _CHECKS.items():
            overridden = [adrf_name for adrf_name in group if adrf_name in own]
            if overridden and name not in own:
                for adrf_name in overridden:
                    warn_once(adrf_name, name)
                setattr(cls, name, check)

    async def check_async_permissions(
        self, request: Request, permissions: Iterable[Any]
    ) -> None:
        for permission in permissions:
            if not await policies.ahas_permission(permission, request, self):
                self._deny(request, permission)

    def check_sync_permissions(
        self, request: Request, permissions: Iterable[Any]
    ) -> None:
        for permission in permissions:
            if not policies.has_permission(permission, request, self):
                self._deny(request, permission)

    async def check_async_object_permissions(
        self, request: Request, permissions: Iterable[Any], obj: Any
    ) -> None:
        for permission in permissions:
            if not await policies.ahas_object_permission(
                permission, request, self, obj
            ):
                self._deny(request, permission)

    def check_sync_object_permissions(
        self, request: Request, permissions: Iterable[Any], obj: Any
    ) -> None:
        for permission in permissions:
            if not policies.has_object_permission(permission, request, self, obj):
                self._deny(request, permission)

    async def check_async_throttles(
        self, request: Request, throttles: Iterable[Any]
    ) -> list[float | None]:
        return [
            await _throttle_wait(throttle)
            for throttle in throttles
            if not await policies.aallow_request(throttle, request, self)
        ]

    def check_sync_throttles(
        self, request: Request, throttles: Iterable[Any]
    ) -> list[float | None]:
        return [
            throttle.wait()
            for throttle in throttles
            if not policies.allow_request(throttle, request, self)
        ]

    def _deny(self, request: Request, permission: Any) -> NoReturn:
        self.permission_denied(
            request,
            message=getattr(permission, "message", None),
            code=getattr(permission, "code", None),
        )


class AdrfGenericMixin(AdrfViewMixin):
    """adrf's ``GenericAPIView`` members."""

    if TYPE_CHECKING:
        paginator: Any

    async def get_apaginated_response(self, data: Any) -> Any:
        # The paginator's, as in adrf: an override that calls super() is
        # also the view's aget_paginated_response.
        assert self.paginator is not None  # noqa: S101 -- as in adrf
        return await call_pair(
            self.paginator, "get_paginated_response", "aget_paginated_response", data
        )
