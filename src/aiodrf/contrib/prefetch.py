"""Compatibility import for the batch-enrichment list serializer."""

from aiodrf.contrib.builtin.list_prefetch import PrefetchListSerializer

__all__ = ["PrefetchListSerializer"]
