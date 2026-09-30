"""
What synchronous signal receivers defer on native writes: their
``transaction.on_commit()`` callbacks (django-cleanup's file deletions, the
project's own) and django-cacheops' invalidations (``nox -s native_db``).

Django sends a native write's signals inside django-async-backend's
transaction and runs synchronous receivers in a thread, on Django's
connection. What they defer must wait for the native commit, and be dropped
when it rolls back.
"""

import contextlib
import socket
import threading
from unittest import mock

import pytest
from asgiref.sync import sync_to_async
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connections, transaction
from django.db.models.signals import (
    m2m_changed,
    post_delete,
    post_save,
    pre_delete,
    pre_save,
)
from django.test import override_settings
from django.utils.asyncio import async_unsafe

from aiodrf.test import APIClient, AsyncAPIClient, count_hops
from tests.async_backend.models import Document, Label, Note
from tests.async_backend.test_views import orm

MODEL_SIGNALS = (pre_save, post_save, pre_delete, post_delete, m2m_changed)


class Rejected(Exception):
    pass


def reject(sender, instance, **kwargs):
    # A failure after the row is written, in the same native transaction.
    if instance.name == "rejected":
        raise Rejected


@pytest.fixture
def media(tmp_path):
    with override_settings(MEDIA_ROOT=str(tmp_path)):
        yield tmp_path


@pytest.fixture(params=["asgi", "wsgi"])
def client(request):
    """``await client(method, url, data, format)`` through Django's ASGI or WSGI handler."""
    if request.param == "asgi":
        api = AsyncAPIClient()

        async def call(method, url, data=None, format="multipart"):
            return await getattr(api, method)(url, data, format=format)
    else:
        api = APIClient()

        async def call(method, url, data=None, format="multipart"):
            return await sync_to_async(getattr(api, method))(url, data, format=format)

    return call


@pytest.fixture
def seen():
    """
    The names of the updated or deleted document that Django's connection, not the
    native one, reads when a receiver's ``on_commit()`` callback runs.
    """
    seen = []

    def receiver(sender, instance, using, **kwargs):
        if kwargs.get("created"):
            return
        pk = instance.pk

        # Django runs a synchronous callback outside the event loop.
        @async_unsafe
        def callback():
            seen.append(
                list(Document.objects.filter(pk=pk).values_list("name", flat=True))
            )

        transaction.on_commit(callback, using=using)

    post_save.connect(receiver, sender=Document)
    post_delete.connect(receiver, sender=Document)
    yield seen
    post_save.disconnect(receiver, sender=Document)
    post_delete.disconnect(receiver, sender=Document)


@pytest.fixture
def rejecting():
    post_save.connect(reject, sender=Document)
    yield
    post_save.disconnect(reject, sender=Document)


async def document(name="first"):
    return await orm(
        Document.objects.create, name=name, file=ContentFile(b"old", name="old.txt")
    )


async def test_a_rolled_back_update_keeps_the_old_file_and_runs_no_callback(
    client, media, seen, rejecting
):
    doc = await document()
    old = media / doc.file.name
    with pytest.raises(Rejected):
        await client(
            "patch",
            f"/documents/{doc.pk}/",
            {"name": "rejected", "file": SimpleUploadedFile("new.txt", b"new")},
        )
    stored = await orm(Document.objects.get, pk=doc.pk)
    assert (stored.name, stored.file.name) == ("first", doc.file.name)
    assert old.exists()
    assert seen == []


async def test_an_update_runs_the_callbacks_after_the_commit(client, media, seen):
    doc = await document()
    old = media / doc.file.name
    response = await client(
        "patch",
        f"/documents/{doc.pk}/",
        {"name": "second", "file": SimpleUploadedFile("new.txt", b"new")},
    )
    assert response.status_code == 200, response.content
    stored = await orm(Document.objects.get, pk=doc.pk)
    assert not old.exists()
    assert (media / stored.file.name).read_bytes() == b"new"
    # The callback ran once the native transaction had committed.
    assert seen == [["second"]]


async def test_a_delete_runs_the_callbacks_after_the_commit(client, media, seen):
    doc = await document()
    old = media / doc.file.name
    response = await client("delete", f"/documents/{doc.pk}/")
    assert response.status_code == 204, response.content
    assert not old.exists()
    assert seen == [[]]


async def test_to_many_writes_run_the_callbacks_after_the_commit(client):
    # m2m_changed, sent from aset_many(); with and without ATOMIC_SAVE, as
    # aset_many() opens a native transaction of its own.
    label = await orm(Label.objects.create, name="a")
    seen = []

    def receiver(sender, instance, action, pk_set, **kwargs):
        if action == "post_add":
            transaction.on_commit(
                lambda: seen.append(list(instance.labels.values_list("pk", flat=True)))
            )

    m2m_changed.connect(receiver, sender=Note.labels.through)
    try:
        for atomic_save in (True, False):
            note = await orm(Note.objects.create, text="n")
            with override_settings(AIODRF={"ATOMIC_SAVE": atomic_save}):
                response = await client(
                    "patch", f"/notes/{note.pk}/", {"labels": [label.pk]}, format="json"
                )
            assert response.status_code == 200, response.content
    finally:
        m2m_changed.disconnect(receiver, sender=Note.labels.through)
    assert seen == [[label.pk], [label.pk]]


# -- django-cacheops ----------------------------------------------------------------------


def redis_is_up():
    try:
        socket.create_connection(("127.0.0.1", 6380), timeout=0.2).close()
    except OSError:
        return False
    return True


@pytest.fixture
def cacheops():
    if not redis_is_up():
        pytest.skip("Redis is not reachable on 6380")
    from cacheops import invalidate_all

    with override_settings(CACHEOPS_ENABLED=True):
        invalidate_all()
        yield
        invalidate_all()


def in_another_thread(func):
    # Another request's read: its own thread and connection.
    result = []

    def run():
        try:
            result.append(func())
        finally:
            connections.close_all()

    thread = threading.Thread(target=run)
    thread.start()
    thread.join()
    return result[0]


async def test_cacheops_invalidates_after_the_commit(client, media, cacheops):
    doc = await document()

    def cached_name():
        return Document.objects.get(pk=doc.pk).name

    await orm(cached_name)
    reads = []

    def concurrent_read(sender, instance, **kwargs):
        # Before the native commit, another request reads the row (the
        # committed, old one) and caches it.
        reads.append(in_another_thread(cached_name))

    post_save.connect(concurrent_read, sender=Document)
    try:
        response = await client("patch", f"/documents/{doc.pk}/", {"name": "second"})
    finally:
        post_save.disconnect(concurrent_read, sender=Document)
    assert response.status_code == 200, response.content
    assert reads == ["first"]
    # The invalidation came after the commit: nothing stale is left cached.
    assert await orm(cached_name) == "second"


# -- Costs --------------------------------------------------------------------------------


async def test_hops(media):
    doc = await document()
    api = AsyncAPIClient()
    with count_hops() as hops:
        response = await api.patch(
            f"/documents/{doc.pk}/", {"name": "second"}, format="json"
        )
    assert response.status_code == 200, response.content
    update = list(hops.calls)
    with contextlib.ExitStack() as stack:
        for signal in MODEL_SIGNALS:
            stack.enter_context(mock.patch.object(signal, "receivers", []))
        with count_hops() as hops:
            response = await api.patch(
                f"/documents/{doc.pk}/", {"name": "third"}, format="json"
            )
    assert response.status_code == 200, response.content
    without_receivers = list(hops.calls)
    # The suite installs django-cleanup and django-cacheops: Django's
    # transaction is entered before the native write and left after it, two
    # hops between the validation and the representation. Without model
    # signal receivers there is nothing to defer.
    assert len(without_receivers) == 2, without_receivers
    assert len(update) == 4, update
    assert [call.rsplit(".", 1)[-1] for call in update[1:3]] == [
        "__enter__",
        "__exit__",
    ], update
