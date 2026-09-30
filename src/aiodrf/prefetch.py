"""Compatibility imports for the optional queryset optimizer."""

from aiodrf.contrib.builtin.prefetch import (
    auto_prefetch,
    forget_lookups,
    related_lookups,
)

__all__ = ["auto_prefetch", "forget_lookups", "related_lookups"]
