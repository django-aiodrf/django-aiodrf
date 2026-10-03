"""
DRF and Django classes known to do no blocking I/O.

Registration is by exact defining class (see :func:`aiodrf.utils.is_pure`),
so user subclasses that override these methods are not affected.

This module must not import ``rest_framework.views``: that module imports
every class named in DRF's ``DEFAULT_*_CLASSES`` settings while its class
body runs, so a project's policy module importing anything from aiodrf that
leads here would find itself half imported.
"""

from django.core.cache.backends.dummy import DummyCache
from django.core.cache.backends.locmem import LocMemCache
from rest_framework import (
    negotiation,
    pagination,
    parsers,
    permissions,
    renderers,
    throttling,
    versioning,
)
from rest_framework.request import ForcedAuthentication

from aiodrf.throttling import FixedWindowRateThrottle
from aiodrf.utils import register_pure, register_pure_method

register_pure(
    # Permissions that only look at ``request.user``, which aiodrf has
    # authenticated before they run. Their methods call nothing else, so a
    # subclass adding ``has_object_permission`` keeps ``has_permission`` pure.
    permissions.BasePermission,
    permissions.AllowAny,
    permissions.IsAuthenticated,
    permissions.IsAdminUser,
    permissions.IsAuthenticatedOrReadOnly,
    # ``force_authenticate()`` of DRF's test clients: returns the user it was
    # given. Without it, the hops a test counts would include one the view
    # never costs in production.
    ForcedAuthentication,
    leaf=True,
)
register_pure(
    # JSON encoding is CPU work. (A subclass that sets ``encoder_class`` is
    # not covered: the encoder is code.)
    renderers.JSONRenderer,
    # Decoding a body Django kept in memory (see ``Request._parses_inline``).
    parsers.JSONParser,
    parsers.FormParser,
    # Header, URL and query string inspection.
    negotiation.DefaultContentNegotiation,
    versioning.AcceptHeaderVersioning,
    versioning.URLPathVersioning,
    versioning.NamespaceVersioning,
    versioning.HostNameVersioning,
    versioning.QueryParameterVersioning,
    # Caches that live in the process: DRF's rate throttles on top of them
    # do no I/O (see ``policies._is_pure_throttle``).
    LocMemCache,
    DummyCache,
)
# DRF's SearchFilter and OrderingFilter are deliberately absent: they call
# ``get_search_fields`` / ``get_default_valid_fields`` (which builds the
# view's serializer), so they are only as pure as the project's overrides.
# Filtering happens in the hop that evaluates the queryset anyway.

# Paginators query the database in ``paginate_queryset`` only; building the
# response afterwards reads what that call stored on the paginator.
for _paginator in (
    pagination.PageNumberPagination,
    pagination.LimitOffsetPagination,
    pagination.CursorPagination,
):
    register_pure_method(_paginator, "get_paginated_response")

# A denying throttle's ``wait()`` is arithmetic on what its ``allow_request``
# stored (``BaseThrottle.wait`` returns None).
for _throttle in (
    throttling.BaseThrottle,
    throttling.SimpleRateThrottle,
    FixedWindowRateThrottle,
):
    register_pure_method(_throttle, "wait", leaf=True)
