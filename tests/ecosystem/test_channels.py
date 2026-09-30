"""
aiodrf permissions in djangochannelsrestframework (DCRF) WebSocket consumers.

DCRF wraps DRF permission classes and calls their synchronous
``has_permission`` in a thread (``database_sync_to_async``), with a plain
Django ``HttpRequest`` built from the connection's scope. aiodrf's bridge
must make a permission that only implements ``ahas_permission`` decide there.
"""

import warnings

import pytest
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated

from aiodrf.permissions import BasePermission
from tests.testapp.models import Author

# DCRF 1.3.0 calls ``asyncio.iscoroutinefunction``, deprecated in Python 3.14,
# when it is imported and on every message.
DCRF_DEPRECATION = "'asyncio.iscoroutinefunction' is deprecated"
with warnings.catch_warnings():
    warnings.filterwarnings("ignore", DCRF_DEPRECATION, DeprecationWarning)
    from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
    from djangochannelsrestframework.mixins import ListModelMixin

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.filterwarnings(f"ignore:{DCRF_DEPRECATION}:DeprecationWarning"),
]


class AuthorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Author
        fields = ["name"]


class OpenLibrary(BasePermission):
    """Async only: allows while an author named "open" exists."""

    async def ahas_permission(self, request, view):
        return await Author.objects.filter(name="open").aexists()


class Authors(ListModelMixin, GenericAsyncAPIConsumer):
    queryset = Author.objects.order_by("name")
    serializer_class = AuthorSerializer
    permission_classes = (OpenLibrary,)


class AuthenticatedOrOpen(Authors):
    permission_classes = (IsAuthenticated | OpenLibrary,)


async def connect(consumer):
    communicator = WebsocketCommunicator(consumer.as_asgi(), "/ws/authors/")
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    return communicator, connected


@pytest.mark.parametrize("consumer", [Authors, AuthenticatedOrOpen])
async def test_an_async_only_permission_refuses_the_connection(consumer):
    communicator, connected = await connect(consumer)
    assert not connected
    await communicator.disconnect()


@pytest.mark.parametrize("consumer", [Authors, AuthenticatedOrOpen])
async def test_an_async_only_permission_allows_the_connection_and_actions(consumer):
    await Author.objects.acreate(name="open")
    communicator, connected = await connect(consumer)
    assert connected
    await communicator.send_json_to({"action": "list", "request_id": 1})
    reply = await communicator.receive_json_from()
    assert reply["response_status"] == 200
    assert reply["data"] == [{"name": "open"}]
    await communicator.disconnect()
