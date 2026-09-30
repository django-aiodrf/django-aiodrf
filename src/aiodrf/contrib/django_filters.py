"""
django-filter in async views.

Nothing needs adapting: aiodrf runs a view's synchronous filter backends in
the thread hop that evaluates the queryset, so a ``FilterSet`` with model
choice filters, ``method=`` filters, a custom form or its own
``filter_queryset()`` behaves exactly as it does under DRF. This module
exists so that ``aiodrf.contrib.django_filters`` can be imported wherever
``django_filters.rest_framework`` was.
"""

from django_filters.rest_framework import DjangoFilterBackend, FilterSet, filters

__all__ = ["DjangoFilterBackend", "FilterSet", "filters"]
