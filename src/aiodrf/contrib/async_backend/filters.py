"""
django-filter for native querysets.

django-filter's ``FilterSet.filter_queryset`` asserts that every filter
returns Django's ``QuerySet`` class; django-async-backend's queryset is a
class of its own with the same filtering API. This ``FilterSet`` is
django-filter's without that check, and ``DjangoFilterBackend`` builds
automatic filter sets from it and requires it of a view's
``filterset_class``.
"""

from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django_filters.rest_framework import DjangoFilterBackend as _DjangoFilterBackend
from django_filters.rest_framework import FilterSet as _FilterSet

__all__ = ["DjangoFilterBackend", "FilterSet"]


class FilterSet(_FilterSet):
    def filter_queryset(self, queryset: Any) -> Any:
        for name, value in self.form.cleaned_data.items():
            queryset = self.filters[name].filter(queryset, value)
        return queryset


class DjangoFilterBackend(_DjangoFilterBackend):
    filterset_base = FilterSet

    def get_filterset_class(self, view: Any, queryset: Any = None) -> Any:
        filterset_class = super().get_filterset_class(view, queryset)
        if filterset_class is not None and not issubclass(filterset_class, FilterSet):
            raise ImproperlyConfigured(
                f"{type(view).__qualname__}.filterset_class must subclass "
                "aiodrf.contrib.async_backend.filters.FilterSet: django-filter's "
                "FilterSet refuses querysets that are not Django's QuerySet class."
            )
        return filterset_class
