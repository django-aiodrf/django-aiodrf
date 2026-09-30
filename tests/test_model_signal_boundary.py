"""Model signals follow the save boundary; sync receivers must stay off-loop."""

import asyncio
import threading

import pytest
from django.db.models.signals import post_save
from rest_framework import serializers

from aiodrf import aio
from tests.testapp.models import Author


@pytest.mark.parametrize("save", ["orm", "serializer"])
async def test_sync_and_async_model_receivers_use_the_correct_threads(
    save, worker_connections
):
    loop_thread = threading.get_ident()
    seen = []

    def synchronous(sender, instance, **kwargs):
        seen.append(("sync", threading.get_ident(), instance.pk))

    async def asynchronous(sender, instance, **kwargs):
        await asyncio.sleep(0)
        seen.append(("async", threading.get_ident(), instance.pk))

    class Input(serializers.ModelSerializer):
        class Meta:
            model = Author
            fields = ["name"]

    post_save.connect(synchronous, sender=Author)
    post_save.connect(asynchronous, sender=Author)
    try:
        if save == "orm":
            instance = await Author.objects.acreate(name="saved")
        else:
            serializer = Input(data={"name": "saved"})
            assert await aio.is_valid(serializer)
            instance = await aio.save(serializer)
    finally:
        post_save.disconnect(synchronous, sender=Author)
        post_save.disconnect(asynchronous, sender=Author)
    assert [row[0] for row in seen] == ["sync", "async"]
    assert seen[0][1] != loop_thread
    assert seen[1][1] == loop_thread
    assert {row[2] for row in seen} == {instance.pk}
