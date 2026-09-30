"""
django-async-backend 6.1.5 against the integration gates: what works with aiodrf
today, and what an integration would have to solve. Each test pins the
observed behaviour, so a new release of the package that changes it fails
here first.

Run in the isolated environment (not a dependency of aiodrf)::

    DJANGO_SETTINGS_MODULE=tests.native_db_gates.settings PYTHONPATH=.:src \\
        .nox/native_db/bin/python -m pytest --no-migrations tests/native_db_gates
"""

import asyncio
import functools

import psycopg
import pytest
from django.core.exceptions import SynchronousOnlyOperation
from django.db import IntegrityError, connections
from django.db.models.signals import post_save
from rest_framework import serializers

from aiodrf import generics
from aiodrf.pagination import PageNumberPagination
from aiodrf.test import AsyncAPIRequestFactory
from aiodrf.utils import run_sync
from tests.testapp.models import Author, Book

pytest.importorskip("django_async_backend")
ADMIN = "host=127.0.0.1 port=55433 user=bench password=bench dbname=bench"
pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "other"])


def native(test):
    """
    Close the test's native connections in the task that owns them: they are
    bound to the first task that used them, and the test database cannot be
    dropped while any session is open.
    """

    @functools.wraps(test)
    async def run():
        from django_async_backend.db import async_connections

        try:
            await test()
        finally:
            for connection in async_connections.all():
                await connection.close()
            # aiodrf's views query in its worker thread, with Django's connections.
            await run_sync(connections.close_all)()

    return run


class AuthorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Author
        fields = ["id", "name"]


class Public:
    authentication_classes = []
    permission_classes = []
    serializer_class = AuthorSerializer


async def call(view):
    return await view(AsyncAPIRequestFactory().get("/"))


async def authors(*names):
    return [await Author.async_objects.acreate(name=name) for name in names]


# -- Gate 1: generic views, pagination, filtering ------------------------------------


@native
async def test_a_native_queryset_does_not_fit_aiodrfs_generic_list():
    # aiodrf evaluates the queryset in its worker, synchronously, as DRF does.
    await authors("Ada")

    class NativeList(Public, generics.ListAPIView):
        queryset = Author.async_objects.order_by("name")

    with pytest.raises(TypeError, match="not iterable synchronously"):
        await call(NativeList.as_view())


@native
async def test_nor_djangos_paginator():
    await authors("Ada", "Bo")

    class Pages(PageNumberPagination):
        page_size = 1

    class NativePages(Public, generics.ListAPIView):
        queryset = Author.async_objects.order_by("name")
        pagination_class = Pages

    with pytest.raises(TypeError, match="len"):
        await call(NativePages.as_view())


@native
async def test_rows_the_view_materializes_itself_are_served():
    # What the benchmark adapter does: an explicit, bounded materialization.
    await authors("Bo", "Ada")

    class Materialized(Public, generics.ListAPIView):
        async def aget_queryset(self):
            return [
                author async for author in Author.async_objects.order_by("name")[:10]
            ]

    response = await call(Materialized.as_view())
    assert [row["name"] for row in response.data] == ["Ada", "Bo"]


# -- Gate 2: relations, validation, signals, errors -----------------------------------


@native
async def test_lazy_relations_of_native_rows_use_the_synchronous_connection():
    (author,) = await authors("Ada")
    book = await Book.async_objects.acreate(title="T", isbn="1", author=author)
    loaded = await Book.async_objects.aget(pk=book.pk)
    with pytest.raises(SynchronousOnlyOperation):
        _ = loaded.author.name
    joined = await Book.async_objects.select_related("author").aget(pk=book.pk)
    assert joined.author.name == "Ada"


@native
async def test_there_is_no_prefetch_related():
    # DRF serializers of reverse and many-to-many relations rely on it.
    assert not hasattr(Author.async_objects.all(), "prefetch_related")


@native
async def test_model_signals_and_integrity_errors_are_djangos():
    created = []

    def receiver(sender, **kwargs):
        created.append(kwargs["created"])

    post_save.connect(receiver, sender=Author)
    try:
        (author,) = await authors("Ada")
    finally:
        post_save.disconnect(receiver, sender=Author)
    assert created == [True]
    await Book.async_objects.acreate(title="T", isbn="1", author=author)
    with pytest.raises(IntegrityError):
        await Book.async_objects.acreate(title="T2", isbn="1", author=author)


# -- Gate 3: transactions, aliases, cancellation, tasks -------------------------------


@native
async def test_nested_async_atomic_rolls_back():
    from django_async_backend.db.transaction import async_atomic

    async def write_then_fail():
        async with async_atomic():
            await Author.async_objects.acreate(name="outer")
            async with async_atomic():
                await Author.async_objects.acreate(name="inner")
            raise RuntimeError

    with pytest.raises(RuntimeError):
        await write_then_fail()
    assert await Author.async_objects.acount() == 0


@native
async def test_another_alias():
    await Author.async_objects.using("other").acreate(name="Ada")
    assert await Author.async_objects.using("other").acount() == 1
    assert await Author.async_objects.acount() == 0


def _running_sleeps():
    with psycopg.connect(ADMIN) as connection:
        return connection.execute(
            "SELECT count(*) FROM pg_stat_activity WHERE query LIKE '%pg_sleep%' "
            "AND state = 'active' AND pid <> pg_backend_pid()"
        ).fetchone()[0]


def _sessions():
    with psycopg.connect(ADMIN) as connection:
        rows = connection.execute(
            "SELECT pid FROM pg_stat_activity WHERE datname = 'test_aiodrf' "
            "AND pid <> pg_backend_pid()"
        ).fetchall()
    return {pid for (pid,) in rows}


@native
async def test_a_timeout_cancels_the_query_on_the_server():
    await authors("Ada")
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.3):
            await Author.async_objects.extra(where=["pg_sleep(3) IS NOT NULL"]).acount()
    await asyncio.sleep(0.2)
    assert await asyncio.to_thread(_running_sleeps) == 0
    assert await Author.async_objects.acount() == 1


@native
async def test_a_connection_belongs_to_the_task_that_used_it_first():
    # ``asyncio.gather``, ``TaskGroup`` and aiodrf's ConcurrentListSerializer
    # run work in other tasks of the same request.
    await authors("Ada")
    before = await asyncio.to_thread(_sessions)
    with pytest.raises(RuntimeError, match="owned by another task"):
        await asyncio.gather(Author.async_objects.acount(), Book.async_objects.acount())
    # Refused before a connection is opened for the other task.
    assert await asyncio.to_thread(_sessions) == before
