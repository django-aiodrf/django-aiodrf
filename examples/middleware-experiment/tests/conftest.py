"""Test fixtures and resource cleanup for the middleware experiment example."""

import httpx
import pytest
from asgi_lifespan import LifespanManager
from project.asgi import application


@pytest.fixture
async def client():
    async with (
        LifespanManager(application) as manager,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=manager.app), base_url="http://testserver"
        ) as http,
    ):
        yield http
