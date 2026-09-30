"""
Pagination for async views.

DRF's paginators need no async subclasses: aiodrf calls
``paginate_queryset`` in one thread hop, which counts and slices the
queryset with DRF's exact semantics (error messages, ``self.page``, cursor
positions), and builds the paginated response inline. Django's
``AsyncPaginator`` would spend a hop on the count and another on the page.

Subclass :class:`BasePagination` to implement ``apaginate_queryset``
yourself, for example against a data source with a native async client.
"""

from typing import Any

from django.db.models import QuerySet
from rest_framework import pagination
from rest_framework.pagination import (
    CursorPagination,
    LimitOffsetPagination,
    PageNumberPagination,
)
from rest_framework.request import Request
from rest_framework.views import APIView

from aiodrf.utils import bridge_base, bridges_to

__all__ = [
    "BasePagination",
    "CursorPagination",
    "LimitOffsetPagination",
    "PageNumberPagination",
]

bridge_base(pagination.BasePagination)


@bridge_base
class BasePagination(pagination.BasePagination):
    @bridges_to("apaginate_queryset")
    def paginate_queryset(
        self, queryset: Any, request: Request, view: APIView | None = None
    ) -> list[Any] | None:
        return super().paginate_queryset(queryset, request, view=view)

    async def apaginate_queryset(
        self, queryset: QuerySet[Any], request: Request, view: APIView | None = None
    ) -> list[Any] | None:  # pragma: no cover
        raise NotImplementedError("apaginate_queryset() must be implemented.")
