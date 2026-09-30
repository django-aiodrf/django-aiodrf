"""Native Valkey cache adapters and lifespan-owned django-valkey pools.

Configure ``CONNECTION_FACTORY`` with ``LifespanConnectionFactory`` and create
one AsyncValkeyCache per worker lifespan. The vendor client retains its pool;
this factory bypasses the vendor's process-global, URL-only pool registry.
It does not change Django's cache middleware or install global patches.
"""

from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django_valkey.async_cache.pool import AsyncConnectionFactory
from valkey.asyncio import Valkey, ValkeyCluster
from valkey.asyncio.connection import BlockingConnectionPool, ConnectionPool
from valkey.asyncio.retry import Retry
from valkey.asyncio.sentinel import Sentinel
from valkey.backoff import NoBackoff

from aiodrf.contrib._cache_backend import NativeCache

__all__ = ["AsyncValkeyCache", "LifespanConnectionFactory"]


class AsyncValkeyCache(NativeCache):
    """Native Valkey cache with awaited callbacks and Sentinel/Cluster support.

    Uses valkey-py's async driver, not django-valkey's client plugin system.
    Keep the vendor backend and LifespanConnectionFactory when its additional
    commands or serializers are required. Both use Django's cache interface.
    """

    client_class = Valkey
    pool_class = ConnectionPool
    blocking_pool_class = BlockingConnectionPool
    sentinel_class = Sentinel
    cluster_class = ValkeyCluster
    retry_class = Retry
    no_backoff_class = NoBackoff


class LifespanConnectionFactory(AsyncConnectionFactory):
    """Reuse pools within one lifespan without the vendor's global registry."""

    def __init__(self, options: dict[str, Any]) -> None:
        super().__init__(options)
        if not issubclass(self.pool_cls, ConnectionPool):
            raise ImproperlyConfigured(
                "LifespanConnectionFactory requires an async Valkey pool."
            )
        self._owned_pools: dict[str, ConnectionPool] = {}

    def get_or_create_connection_pool(self, params: dict[str, Any]) -> ConnectionPool:
        # No await between lookup and publication: this factory belongs to one
        # event loop. Separate instances never share pools, even for one URL.
        url = params["url"]
        if url not in self._owned_pools:
            self._owned_pools[url] = self.get_connection_pool(params.copy())
        return self._owned_pools[url]
