"""The documented HTTPX/asgi-lifespan test recipe against the real ASGI root."""

from contextlib import asynccontextmanager

import httpx
from asgi_lifespan import LifespanManager
from django.test import override_settings

from aiodrf.asgi import get_asgi_application
from tests.test_lifespan_context import Resource


async def test_manager_app_propagates_state_and_closes_the_context():
    import asyncio

    resource = Resource(asyncio.get_running_loop())

    @asynccontextmanager
    async def lifespan():
        try:
            yield resource
        finally:
            resource.closed = True

    with override_settings(ROOT_URLCONF="tests.test_lifespan_context"):
        application = get_asgi_application(lifespan=lifespan)
        async with LifespanManager(application) as manager:
            transport = httpx.ASGITransport(app=manager.app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                response = await client.get("/state/")
                assert response.status_code == 200
                assert response.json() == {"live": True}
                assert not resource.closed
    assert resource.closed
