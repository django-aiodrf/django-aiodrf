"""DRF permission classes with awaitable extension hooks."""

from typing import TYPE_CHECKING, Any

from rest_framework import permissions
from rest_framework.permissions import (  # noqa: F401
    AND,
    NOT,
    OR,
    SAFE_METHODS,
    AllowAny,
    BasePermissionMetaclass,
    DjangoModelPermissions,
    DjangoModelPermissionsOrAnonReadOnly,
    DjangoObjectPermissions,
    IsAdminUser,
    IsAuthenticated,
    IsAuthenticatedOrReadOnly,
)
from rest_framework.request import Request

from aiodrf.utils import bridge_base, bridges_to

if TYPE_CHECKING:
    # Not at run time: ``rest_framework.views`` imports this module while its
    # ``APIView`` class body resolves the ``DEFAULT_*_CLASSES`` settings.
    from rest_framework.views import APIView

__all__ = [
    "AND",
    "NOT",
    "OR",
    "SAFE_METHODS",
    "AllowAny",
    "BasePermission",
    "DjangoModelPermissions",
    "DjangoModelPermissionsOrAnonReadOnly",
    "DjangoObjectPermissions",
    "IsAdminUser",
    "IsAuthenticated",
    "IsAuthenticatedOrReadOnly",
]

bridge_base(permissions.BasePermission)


@bridge_base
class BasePermission(permissions.BasePermission):
    """
    Base class for permissions implemented with ``async def ahas_permission``
    and/or ``async def ahas_object_permission``.

    Composes with DRF permissions through ``&``, ``|`` and ``~``. The sync
    methods delegate to the async ones, so a permission that is only
    implemented asynchronously is still enforced when DRF checks it
    synchronously (the browsable API, ``OPTIONS`` metadata, schema generation)
    instead of falling back to DRF's permissive default.
    """

    @bridges_to("ahas_permission")
    def has_permission(self, request: Request, view: "APIView") -> bool:
        return super().has_permission(request, view)

    @bridges_to("ahas_object_permission")
    def has_object_permission(
        self, request: Request, view: "APIView", obj: Any
    ) -> bool:
        return super().has_object_permission(request, view, obj)

    async def ahas_permission(self, request: Request, view: "APIView") -> bool:
        return True

    async def ahas_object_permission(
        self, request: Request, view: "APIView", obj: Any
    ) -> bool:
        return True
