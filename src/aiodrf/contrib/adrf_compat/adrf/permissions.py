"""
adrf's ``permissions``.

aiodrf evaluates DRF's ``&``, ``|`` and ``~`` with async operands itself, in
order and short-circuiting, so adrf's async operators are DRF's and
``try_convert_operator`` returns its argument.
"""

from typing import Any

from rest_framework.permissions import AND as AAND
from rest_framework.permissions import NOT as ANOT
from rest_framework.permissions import OR as AOR
from rest_framework.permissions import (
    BasePermissionMetaclass as AsyncBasePermissionMetaClass,
)
from rest_framework.permissions import OperandHolder as AsyncOperandHolder
from rest_framework.permissions import SingleOperandHolder as AsyncSingleOperandHolder
from rest_framework.request import Request

from aiodrf.permissions import BasePermission

__all__ = [
    "AAND",
    "ANOT",
    "AOR",
    "AsyncBasePermission",
    "AsyncBasePermissionMetaClass",
    "AsyncOperandHolder",
    "AsyncSingleOperandHolder",
    "is_async_perm_operator",
    "is_perm_operator",
    "try_convert_operator",
]


class AsyncBasePermission(BasePermission):
    """adrf's: an async permission that grants by default."""

    async def has_permission(self, request: Request, view: Any) -> bool:
        return True

    async def has_object_permission(
        self, request: Request, view: Any, obj: Any
    ) -> bool:
        return True


def try_convert_operator(operator_instance: Any) -> Any:
    return operator_instance


def is_perm_operator(operator_instance: Any) -> bool:
    return isinstance(operator_instance, (AAND, AOR, ANOT))


is_async_perm_operator = is_perm_operator
