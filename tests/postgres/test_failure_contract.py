"""Real commits/rollbacks, routed writes, and cancellation of a sync save."""

import asyncio
import threading

import pytest
from django.db import IntegrityError, connections, transaction
from django.test import override_settings
from rest_framework import serializers

from aiodrf import aio
from aiodrf.utils import run_sync
from tests.testapp.models import Author, Tag

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "other"])


@pytest.fixture(autouse=True)
async def close_worker_connections():
    yield
    await run_sync(connections.close_all)()


class OtherRouter:
    def db_for_write(self, model, **hints):
        return "other"


@pytest.mark.parametrize("alias", ["default", "other"])
async def test_nested_atomic_rollback_discards_inner_callbacks_and_connection_recovers(
    alias,
):
    callbacks = []

    def work():
        assert connections[alias].vendor == "postgresql"
        with transaction.atomic(using=alias):
            transaction.on_commit(lambda: callbacks.append("outer-first"), using=alias)
            for _ in range(3):
                try:
                    with transaction.atomic(using=alias):
                        Tag.objects.using(alias).create(name="duplicate")
                        transaction.on_commit(
                            lambda: callbacks.append("rolled-back"), using=alias
                        )
                        Tag.objects.using(alias).create(name="duplicate")
                except IntegrityError:
                    assert not Tag.objects.using(alias).exists()
            transaction.on_commit(lambda: callbacks.append("outer-last"), using=alias)
        assert not connections[alias].in_atomic_block
        assert not connections[alias].needs_rollback
        Author.objects.using(alias).create(name="connection still usable")

    await run_sync(work)()
    assert callbacks == ["outer-first", "outer-last"]
    assert await Author.objects.using(alias).acount() == 1


@override_settings(DATABASE_ROUTERS=[OtherRouter()])
async def test_routed_persistence_failure_rolls_back_the_nondefault_database():
    class Input(serializers.ModelSerializer):
        class Meta:
            model = Author
            fields = ["name"]

        def create(self, data):
            author = super().create(data)
            Tag.objects.create(name="duplicate")
            Tag.objects.create(name="duplicate")
            return author

    serializer = Input(data={"name": "must roll back"})
    assert await aio.is_valid(serializer)
    with pytest.raises(IntegrityError):
        await aio.save(serializer)
    for alias in ("default", "other"):
        assert not await Author.objects.using(alias).aexists()
        assert not await Tag.objects.using(alias).aexists()


async def test_request_cancellation_does_not_undo_a_sync_workers_commit():
    started, release = threading.Event(), threading.Event()
    committed = []

    class Input(serializers.ModelSerializer):
        class Meta:
            model = Author
            fields = ["name"]

        def create(self, data):
            author = super().create(data)
            transaction.on_commit(lambda: committed.append(author.pk))
            started.set()
            assert release.wait(3), "The controller must release the worker"
            return author

    serializer = Input(data={"name": "committed after disconnect"})
    assert await aio.is_valid(serializer)
    saving = asyncio.create_task(aio.save(serializer))
    try:
        assert await asyncio.to_thread(started.wait, 3)
        saving.cancel()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await saving
    count = await Author.objects.acount()
    assert count == 1
    assert len(committed) == 1


async def test_validation_failure_never_enters_the_save_boundary():
    class Input(serializers.ModelSerializer):
        class Meta:
            model = Author
            fields = ["name"]

        def create(self, data):
            raise AssertionError("invalid data must not be saved")

    serializer = Input(data={"name": ""})
    assert not await aio.is_valid(serializer)
    with pytest.raises(AssertionError, match="invalid data"):
        await aio.save(serializer)
    assert not await Author.objects.aexists()
