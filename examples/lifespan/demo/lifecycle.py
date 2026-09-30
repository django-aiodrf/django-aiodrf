"""Application-owned startup resources and shutdown cleanup."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx


@dataclass(frozen=True, slots=True)
class Resources:
    http: httpx.AsyncClient


def local_service(request):
    return httpx.Response(200, json={"value": 42})


@asynccontextmanager
async def lifespan() -> AsyncGenerator[Resources, None]:
    async with httpx.AsyncClient(
        base_url="http://local-example",
        transport=httpx.MockTransport(local_service),
        timeout=2,
    ) as http:
        yield Resources(http)
