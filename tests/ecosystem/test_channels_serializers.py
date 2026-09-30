"""
aiodrf serializers' async API inside djangochannelsrestframework (DCRF)
consumer actions.

A DCRF action is an ``async def`` on the consumer; DCRF's own examples wrap
the ORM and DRF serializers in ``database_sync_to_async``. With aiodrf the
action awaits the serializer: ``ais_valid()``, ``asave()`` and ``adata()`` on
aiodrf serializers, ``aiodrf.aio.is_valid``/``save``/``data`` for plain DRF
serializers. The same actions written the DCRF way are the reference: the
payloads, statuses and error bodies must be identical.
"""

import warnings

import pytest
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from rest_framework import serializers as drf_serializers
from rest_framework.permissions import AllowAny

from aiodrf import aio, serializers
from aiodrf.test import count_hops
from tests.testapp.models import Author

# DCRF 1.3.0 calls ``asyncio.iscoroutinefunction``, deprecated in Python 3.14,
# when it is imported and on every message.
DCRF_DEPRECATION = "'asyncio.iscoroutinefunction' is deprecated"
with warnings.catch_warnings():
    warnings.filterwarnings("ignore", DCRF_DEPRECATION, DeprecationWarning)
    from djangochannelsrestframework.decorators import action as dcrf_action
    from djangochannelsrestframework.generics import GenericAsyncAPIConsumer


def action(**kwargs):
    # DCRF's decorator makes the same deprecated call when it is applied.
    def decorate(func):
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", DCRF_DEPRECATION, DeprecationWarning)
            return dcrf_action(**kwargs)(func)

    return decorate


pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.filterwarnings(f"ignore:{DCRF_DEPRECATION}:DeprecationWarning"),
]

TAKEN = "This name is taken."


class AsyncAuthorSerializer(serializers.ModelSerializer):
    """An aiodrf serializer whose field validator is async."""

    class Meta:
        model = Author
        fields = ["id", "name"]

    async def validate_name(self, value):
        taken = Author.objects.filter(name=value)
        if self.instance is not None:
            taken = taken.exclude(pk=self.instance.pk)
        if await taken.aexists():
            raise drf_serializers.ValidationError(TAKEN)
        return value


class PlainAuthorSerializer(drf_serializers.ModelSerializer):
    """The same serializer for DRF: a synchronous validator."""

    class Meta:
        model = Author
        fields = ["id", "name"]

    def validate_name(self, value):
        taken = Author.objects.filter(name=value)
        if self.instance is not None:
            taken = taken.exclude(pk=self.instance.pk)
        if taken.exists():
            raise drf_serializers.ValidationError(TAKEN)
        return value


class Base(GenericAsyncAPIConsumer):
    queryset = Author.objects.order_by("name")
    permission_classes = (AllowAny,)


class AiodrfAuthors(Base):
    """Actions awaiting aiodrf serializers."""

    @action()
    async def create(self, data, **kwargs):
        serializer = AsyncAuthorSerializer(data=data)
        await serializer.ais_valid(raise_exception=True)
        await serializer.asave()
        return await serializer.adata(), 201

    @action()
    async def patch(self, pk, data, **kwargs):
        author = await Author.objects.aget(pk=pk)
        serializer = AsyncAuthorSerializer(author, data=data, partial=True)
        await serializer.ais_valid(raise_exception=True)
        await serializer.asave()
        return await serializer.adata(), 200

    @action()
    async def list(self, **kwargs):
        serializer = AsyncAuthorSerializer(self.get_queryset(**kwargs), many=True)
        return await serializer.adata(), 200


class AioAuthors(Base):
    """Actions awaiting a plain DRF serializer through ``aiodrf.aio``."""

    @action()
    async def create(self, data, **kwargs):
        serializer = PlainAuthorSerializer(data=data)
        await aio.is_valid(serializer, raise_exception=True)
        await aio.save(serializer)
        return await aio.data(serializer), 201

    @action()
    async def patch(self, pk, data, **kwargs):
        author = await Author.objects.aget(pk=pk)
        serializer = PlainAuthorSerializer(author, data=data, partial=True)
        await aio.is_valid(serializer, raise_exception=True)
        await aio.save(serializer)
        return await aio.data(serializer), 200

    @action()
    async def list(self, **kwargs):
        serializer = PlainAuthorSerializer(self.get_queryset(**kwargs), many=True)
        return await aio.data(serializer), 200


class DCRFAuthors(Base):
    """The reference: DRF serializers in ``database_sync_to_async``."""

    @action()
    async def create(self, data, **kwargs):
        return await database_sync_to_async(self._create)(data), 201

    def _create(self, data):
        serializer = PlainAuthorSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return serializer.data

    @action()
    async def patch(self, pk, data, **kwargs):
        return await database_sync_to_async(self._patch)(pk, data), 200

    def _patch(self, pk, data):
        serializer = PlainAuthorSerializer(
            Author.objects.get(pk=pk), data=data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return serializer.data

    @action()
    async def list(self, **kwargs):
        return await database_sync_to_async(self._list)(), 200

    def _list(self):
        return PlainAuthorSerializer(self.get_queryset(), many=True).data


CONSUMERS = {"aiodrf": AiodrfAuthors, "aio": AioAuthors, "dcrf": DCRFAuthors}


async def call(consumer, *messages):
    """Send ``messages`` on one connection; return the replies, ids dropped."""
    communicator = WebsocketCommunicator(consumer.as_asgi(), "/ws/authors/")
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    replies = []
    try:
        for request_id, message in enumerate(messages):
            await communicator.send_json_to({**message, "request_id": request_id})
            reply = await communicator.receive_json_from()
            assert reply.pop("request_id") == request_id
            replies.append(reply)
    finally:
        await communicator.disconnect()
    return replies


def without_ids(reply):
    data = reply["data"]
    if isinstance(data, list):
        data = [{k: v for k, v in item.items() if k != "id"} for item in data]
    elif isinstance(data, dict):
        data = {k: v for k, v in data.items() if k != "id"}
    return {**reply, "data": data}


async def replies_of(name):
    await Author.objects.all().adelete()
    replies = await call(
        CONSUMERS[name],
        {"action": "create", "data": {"name": "Ursula"}},
        {"action": "create", "data": {"name": "Octavia"}},
        {"action": "create", "data": {"name": "Ursula"}},  # taken: async validator
        {"action": "create", "data": {"name": ""}},  # DRF's field validation
        {"action": "list"},
    )
    octavia = await Author.objects.aget(name="Octavia")
    replies += await call(
        CONSUMERS[name],
        {"action": "patch", "pk": octavia.pk, "data": {"name": "Nnedi"}},
        {"action": "patch", "pk": octavia.pk, "data": {"name": "Ursula"}},
    )
    return [without_ids(reply) for reply in replies]


async def test_actions_answer_as_the_dcrf_way_does():
    reference = await replies_of("dcrf")
    assert [reply["response_status"] for reply in reference] == [
        201,
        201,
        400,
        400,
        200,
        200,
        400,
    ]
    assert reference[2]["errors"] == [{"name": [TAKEN]}]
    assert reference[3]["errors"] == [{"name": ["This field may not be blank."]}]
    assert reference[4]["data"] == [{"name": "Octavia"}, {"name": "Ursula"}]
    assert reference[5]["data"] == {"name": "Nnedi"}
    for name in ("aiodrf", "aio"):
        assert await replies_of(name) == reference, name


def counted(consumer):
    """``consumer`` recording the hops of each action it handles."""
    # asgiref's test communicator runs the application in a fresh context,
    # so the hops are counted inside the consumer.
    recorded = []

    class Counted(consumer):
        async def handle_action(self, action, request_id, **kwargs):
            with count_hops() as hops:
                await super().handle_action(action, request_id, **kwargs)
            recorded.append(hops.calls)

    return Counted, recorded


# Each awaited step that runs DRF code is one hop: validation (DRF's field
# checks run in the worker; the async validator is awaited on the loop), the
# save, and the representation. A refused create stops after validation.
# DCRF's ``database_sync_to_async`` is one crossing per action, which aiodrf
# does not count.
EXPECTED_HOPS = [
    ["_try_default_is_valid", "create", "try_data"],
    ["_try_default_is_valid"],
    ["try_data"],
]


@pytest.mark.aiodrf_settings(REPRESENTATION_MODE="thread")
@pytest.mark.parametrize("name", ["aiodrf", "aio"])
async def test_hops(name):
    consumer, recorded = counted(CONSUMERS[name])
    await call(
        consumer,
        {"action": "create", "data": {"name": "Ursula"}},
        {"action": "create", "data": {"name": "Ursula"}},  # taken
        {"action": "list"},
    )
    names = [[hop.rsplit(".", 1)[-1] for hop in calls] for calls in recorded]
    assert names == EXPECTED_HOPS
