"""aiodrf integration with the independent native cache package."""

import os
from uuid import uuid4

import pytest

URL = os.environ.get("AIODRF_TEST_REDIS_URL")
pytestmark = pytest.mark.skipif(not URL, reason="Requires a Redis test service")


async def test_page_middleware_and_lifespan_share_native_pool():
    from tests.ecosystem.cache_contracts import check_page_cache

    await check_page_cache(
        {
            "BACKEND": "aiodrf_async_cache.redis.AsyncRedisCache",
            "LOCATION": URL,
            "KEY_PREFIX": "redis-pages-" + uuid4().hex,
        },
        "django_redis.cache.RedisCache",
    )
