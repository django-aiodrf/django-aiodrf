"""Version-specific Django and DRF compatibility exports."""

from typing import TYPE_CHECKING

import django
import rest_framework
import rest_framework.fields
from django.utils.connection import ConnectionProxy
from django.views import View

if TYPE_CHECKING:
    from django.core.cache.backends.base import BaseCache

DJANGO_VERSION = django.VERSION[:2]
DRF_VERSION = tuple(int(part) for part in rest_framework.VERSION.split(".")[:2])
# DRF 3.17 added ``BigIntegerField`` (ModelSerializer's field for the big
# integer model fields); the class, or None. TODO: remove with DRF 3.16.
BigIntegerField = getattr(rest_framework.fields, "BigIntegerField", None)
# DRF 3.18 added ``REST_FRAMEWORK["LIST_SERIALIZER_ERRORS_AS_DICT"]``.
# TODO: remove with DRF 3.17.
DRF_HAS_LIST_ERRORS_AS_DICT = DRF_VERSION >= (3, 18)

try:  # Django 6.1+
    from django.db.models import FETCH_PEERS, FETCH_RAISE
except ImportError:  # pragma: no cover - depends on the Django version
    FETCH_PEERS = FETCH_RAISE = None  # type: ignore[assignment]

# Kept as a compatibility export for consumers on the supported Django range.
try:  # Django 6.1+
    from django.core.exceptions import FieldFetchBlocked
except ImportError:  # pragma: no cover
    FieldFetchBlocked = None  # type: ignore[assignment,misc]


# The HTTP QUERY method (RFC 10008). While Django's ``View`` does not dispatch
# it, aiodrf's ``APIView`` adds it; nothing else of Django's is changed.
# TODO(django#37232): remove with the Django versions that lack it
# (https://github.com/django/django/pull/21855); ``APIView`` then inherits it.
DJANGO_HAS_QUERY = "query" in View.http_method_names


def resolve_cache(cache: "BaseCache | ConnectionProxy") -> "BaseCache":
    """Return the backend behind ``django.core.cache.cache`` style proxies."""
    if isinstance(cache, ConnectionProxy):
        return cache._connections[cache._alias]
    return cache
