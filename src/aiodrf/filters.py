"""
Filter backends for async views.

Filter backends normally only build lazy querysets, which is CPU work. DRF's
``SearchFilter`` and ``OrderingFilter`` therefore run inline on the event
loop; other synchronous backends run in a thread unless they are declared
``@async_safe``. For django-filter use
:class:`aiodrf.contrib.django_filters.DjangoFilterBackend`.
"""

from typing import Any

from django.db.models import QuerySet
from rest_framework import filters
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.request import Request
from rest_framework.views import APIView

from aiodrf.utils import bridge_base, bridges_to

__all__ = ["BaseFilterBackend", "OrderingFilter", "SearchFilter"]

bridge_base(filters.BaseFilterBackend)


@bridge_base
class BaseFilterBackend(filters.BaseFilterBackend):
    """Base class for filter backends implemented with ``async def afilter_queryset``."""

    @bridges_to("afilter_queryset")
    def filter_queryset(
        self, request: Request, queryset: QuerySet[Any], view: APIView
    ) -> QuerySet[Any]:
        return super().filter_queryset(request, queryset, view)

    async def afilter_queryset(
        self, request: Request, queryset: QuerySet[Any], view: APIView
    ) -> QuerySet[Any]:
        raise NotImplementedError(".afilter_queryset() must be overridden.")
