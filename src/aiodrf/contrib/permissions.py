"""
DRF's model-permission classes with an async ``has_permission`` (opt-in).

``aiodrf.permissions`` exports DRF's own ``DjangoModelPermissions`` family,
whose check runs in a thread hop on every request. These subclasses make the
same decision, in DRF's order, and ask Django's permission backends in that
hop only when a permission is required::

    from aiodrf.contrib.permissions import DjangoModelPermissions

    class BookViewSet(viewsets.ModelViewSet):
        permission_classes = [DjangoModelPermissions]

With DRF's default ``perms_map`` safe methods require no permission, and
``has_perms([])`` is True without asking any backend, so they need no hop.
Everything else goes through Django's synchronous ``has_perms`` in one hop,
so every backend (guardian, rules, ...) is asked exactly as in DRF. Django's
``ahas_perms`` is not used: with the user loaded per request it costs two
thread crossings, and it leaves out backends without async methods.

A subclass that overrides ``has_permission``, ``get_required_permissions``
or ``_queryset`` is code written for DRF and runs as DRF's, in one hop.
"""

from typing import Any

from django.contrib.auth.models import AnonymousUser, PermissionsMixin
from rest_framework import permissions

from aiodrf.utils import call_pair, run_sync, user_defines

__all__ = [
    "DjangoModelPermissions",
    "DjangoModelPermissionsOrAnonReadOnly",
    "DjangoObjectPermissions",
]

# ``has_perms`` implementations whose answer for an empty list is True
# without a backend call; a user model with its own may answer otherwise.
_DJANGO_HAS_PERMS = (PermissionsMixin.has_perms, AnonymousUser.has_perms)


class DjangoModelPermissions(permissions.DjangoModelPermissions):
    async def ahas_permission(self, request: Any, view: Any) -> bool:
        if user_defines(
            self, "has_permission", "get_required_permissions", "_queryset"
        ):
            return await run_sync(self.has_permission)(request, view)
        # DRF's ``has_permission``, step by step.
        user = request.user
        if not user or (not user.is_authenticated and self.authenticated_users_only):
            return False
        if getattr(view, "_ignore_model_permissions", False):
            return True
        queryset = await self._aqueryset(view)
        perms = self.get_required_permissions(request.method, queryset.model)
        if not perms and getattr(type(user), "has_perms", None) in _DJANGO_HAS_PERMS:
            return True
        return await run_sync(user.has_perms)(perms)

    async def _aqueryset(self, view: Any) -> Any:
        # DRF's ``_queryset``; a view's queryset hooks by aiodrf's rules.
        assert (  # noqa: S101 -- preserve DRF's configuration assertion contract
            hasattr(view, "get_queryset") or getattr(view, "queryset", None) is not None
        ), (
            f"Cannot apply {self.__class__.__name__} on a view that does not set "
            "`.queryset` or have a `.get_queryset()` method."
        )
        if hasattr(view, "get_queryset"):
            queryset = await call_pair(view, "get_queryset", "aget_queryset")
            assert queryset is not None, (  # noqa: S101 -- DRF's configuration contract
                f"{view.__class__.__name__}.get_queryset() returned None"
            )
            return queryset
        return view.queryset


class DjangoModelPermissionsOrAnonReadOnly(DjangoModelPermissions):
    authenticated_users_only = False


class DjangoObjectPermissions(
    DjangoModelPermissions, permissions.DjangoObjectPermissions
):
    # Object permissions are DRF's: they run in the action's hop already.
    pass
