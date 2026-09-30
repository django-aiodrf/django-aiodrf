"""Validate explicit query parameters with ordinary DRF serializer fields."""

from rest_filters import Filter, FilterSet
from rest_framework import serializers


class BookFilters(FilterSet):
    title = Filter(serializers.CharField(min_length=2), lookup="icontains")
    author = Filter(
        serializers.CharField(min_length=2), field="author__name", lookup="icontains"
    )
