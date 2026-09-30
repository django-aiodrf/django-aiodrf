"""Resources that live as long as the ASGI server (``AIODRF["LIFESPAN"]``)."""

import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
from django.conf import settings


@dataclass(frozen=True, slots=True)
class Resources:
    stock: httpx.AsyncClient


@asynccontextmanager
async def lifespan() -> AsyncGenerator[Resources, None]:
    # A deterministic local service keeps the default example self-contained.
    # Set STOCK_SERVICE_URL to exercise an actual remote inventory service.
    transport = None if settings.STOCK_SERVICE_URL else httpx.MockTransport(local_stock)
    async with httpx.AsyncClient(
        base_url=settings.STOCK_SERVICE_URL or "http://local-stock",
        transport=transport,
        timeout=5.0,
    ) as stock:
        yield Resources(stock=stock)


def local_stock(request):
    return httpx.Response(
        200, json={sku: len(sku) for sku in json.loads(request.content)}
    )
