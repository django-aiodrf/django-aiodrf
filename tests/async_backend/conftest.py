import pytest
from asgiref.sync import sync_to_async
from django.db import connections

from aiodrf.test import APIClient, AsyncAPIClient


@pytest.fixture(autouse=True)
async def close_native_connections(transactional_db):
    """
    Close what the test itself opened, in the test's task: a native
    connection belongs to the first task that used it, and the test database
    cannot be dropped while a session is open. Requests close theirs through
    django-async-backend's request signals.
    """
    from django_async_backend.db import async_connections

    yield
    for connection in async_connections.all():
        await connection.close()
    await sync_to_async(connections.close_all)()


@pytest.fixture(params=["asgi", "wsgi"])
def api(request):
    """``await api(method, url, data)`` through Django's ASGI or WSGI handler."""
    if request.param == "asgi":
        client = AsyncAPIClient()

        async def call(method, url, data=None):
            return await getattr(client, method)(url, data, format="json")
    else:
        client = APIClient()

        async def call(method, url, data=None):
            return await sync_to_async(getattr(client, method))(
                url, data, format="json"
            )

    call.transport = request.param
    return call
