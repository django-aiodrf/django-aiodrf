"""Own the direct PyMongo async client on the ASGI worker's event loop."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from django.conf import settings
from pymongo import AsyncMongoClient


@dataclass(frozen=True, slots=True)
class Resources:
    mongo: AsyncMongoClient
    database: str


@asynccontextmanager
async def lifespan() -> AsyncGenerator[Resources, None]:
    database = settings.DATABASES["default"]
    async with AsyncMongoClient(
        database["HOST"],
        serverSelectionTimeoutMS=5000,
        maxPoolSize=20,
        waitQueueTimeoutMS=5000,
    ) as client:
        yield Resources(client, database["NAME"])
