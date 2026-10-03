"""Use a native page cache owned by the project's composite lifespan resource."""

from aiodrf_asgi_lifespan.asgi import get_lifespan_state
from aiodrf_async_cache.middleware import (
    AsyncFetchFromCacheMiddleware,
    AsyncUpdateCacheMiddleware,
)
from django.core.exceptions import ImproperlyConfigured

from .lifecycle import Resources


class CacheResourceMixin:
    def get_cache(self, request):
        cache = get_lifespan_state(request, Resources).cache
        if cache is None:
            raise ImproperlyConfigured("Configure an example native cache URL.")
        return cache


class FetchCache(CacheResourceMixin, AsyncFetchFromCacheMiddleware):
    """Fetch the response after other request middleware has run."""


class UpdateCache(CacheResourceMixin, AsyncUpdateCacheMiddleware):
    """Store the response after other response middleware has run."""
