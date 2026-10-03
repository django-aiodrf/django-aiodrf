"""Own optional network clients and close them at ASGI shutdown."""

import os
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

import sentry_sdk
from aiodrf_async_cache.lifespan import cache_lifespan
from aiodrf_async_cache.middleware import AsyncCache
from django.conf import settings
from elasticsearch import AsyncElasticsearch
from opensearchpy import AsyncOpenSearch
from sentry_sdk.integrations.django import DjangoIntegration

from aiodrf.utils import run_sync


@dataclass(frozen=True, slots=True)
class Resources:
    search: AsyncElasticsearch | None = None
    cache: AsyncCache | None = None
    opensearch: AsyncOpenSearch | None = None


@asynccontextmanager
async def lifespan() -> AsyncGenerator[Resources, None]:
    async with AsyncExitStack() as stack:
        search = None
        cache = None
        opensearch = None
        if settings.EXAMPLE_OPENSEARCH_URL:
            opensearch = await stack.enter_async_context(
                AsyncOpenSearch(hosts=[settings.EXAMPLE_OPENSEARCH_URL], timeout=5)
            )
        if settings.EXAMPLE_SEARCH_URL:
            search = await stack.enter_async_context(
                AsyncElasticsearch(settings.EXAMPLE_SEARCH_URL, request_timeout=5)
            )
        if settings.EXAMPLE_CACHE_URL:
            cache = await stack.enter_async_context(cache_lifespan("native"))
        dsn = os.environ.get("EXAMPLE_SENTRY_DSN")
        if dsn:
            sentry_sdk.init(
                dsn=dsn,
                integrations=[DjangoIntegration()],
                send_default_pii=False,
                traces_sample_rate=0.0,
            )
            client = sentry_sdk.get_client()
            stack.push_async_callback(run_sync(client.close), timeout=2)
        yield Resources(search, cache, opensearch)
