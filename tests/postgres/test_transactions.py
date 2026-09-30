"""
``ATOMIC_SAVE`` on PostgreSQL, with real commits.

SQLite forgives what PostgreSQL does not: after a failed statement a
PostgreSQL transaction accepts nothing until it is rolled back, and a
connection left in that state breaks the next request that gets it.
"""

from unittest import mock

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.backends.base.base import BaseDatabaseWrapper
from django.db.models.signals import post_save
from django.test import TransactionTestCase, override_settings
from django.urls import path
from rest_framework.permissions import AllowAny

from aiodrf import serializers, viewsets
from tests.base import AsyncTransport, SyncTransport
from tests.testapp.models import Author, Book, Tag
from tests.testapp.serializers import AuthorSerializer, BookSerializer

committed = []


class TwiceSerializer(AuthorSerializer):
    """Inserts the author, then violates ``Tag.name``'s unique constraint."""

    def create(self, validated_data):
        author = super().create(validated_data)
        Tag.objects.create(name="taken")
        return author


class AnnouncingSerializer(AuthorSerializer):
    def create(self, validated_data):
        author = super().create(validated_data)
        transaction.on_commit(lambda: committed.append(author.pk))
        return author


class AiodrfBookSerializer(serializers.ModelSerializer):
    class Meta(BookSerializer.Meta):
        pass


class Open:
    authentication_classes = []
    permission_classes = [AllowAny]


class Authors(Open, viewsets.ModelViewSet):
    queryset = Author.objects.order_by("pk")
    serializer_class = AuthorSerializer


class TwiceAuthors(Authors):
    serializer_class = TwiceSerializer


class AnnouncingAuthors(Authors):
    serializer_class = AnnouncingSerializer


class NestedAtomicAuthors(Authors):
    def perform_create(self, serializer):
        # A project's own transaction around aiodrf's.
        with transaction.atomic():
            serializer.save()


class Books(Open, viewsets.ModelViewSet):
    queryset = Book.objects.order_by("pk")
    serializer_class = BookSerializer


class AiodrfBooks(Books):
    serializer_class = AiodrfBookSerializer


urlpatterns = [
    path("authors/", Authors.as_view({"get": "list", "post": "create"})),
    path("twice/", TwiceAuthors.as_view({"post": "create"})),
    path("announcing/", AnnouncingAuthors.as_view({"post": "create"})),
    path("nested/", NestedAtomicAuthors.as_view({"post": "create"})),
    path("books/", Books.as_view({"get": "list", "post": "create"})),
    path("aiodrf-books/", AiodrfBooks.as_view({"post": "create"})),
]


class _TransactionTests:
    def setUp(self):
        super().setUp()
        committed.clear()

    def test_the_database_is_postgresql(self):
        assert connection.vendor == "postgresql"

    async def test_a_failed_statement_rolls_the_save_back_and_leaves_the_connection_usable(
        self,
    ):
        await Tag.objects.acreate(name="taken")
        with pytest.raises(IntegrityError):
            await self.api("post", "/twice/", data={"name": "Ursula"})
        assert not await Author.objects.aexists()
        # The next request may get the same connection.
        created = await self.api("post", "/authors/", data={"name": "Octavia"})
        assert created.status_code == 201, created.data
        assert [
            author["name"] for author in (await self.api("get", "/authors/")).data
        ] == ["Octavia"]

    @override_settings(AIODRF={"ATOMIC_SAVE": False})
    async def test_without_atomic_save_the_first_insert_stays(self):
        await Tag.objects.acreate(name="taken")
        with pytest.raises(IntegrityError):
            await self.api("post", "/twice/", data={"name": "Ursula"})
        assert await Author.objects.filter(name="Ursula").aexists()

    async def test_on_commit_runs_once_the_save_is_committed(self):
        created = await self.api("post", "/announcing/", data={"name": "Ursula"})
        assert created.status_code == 201
        assert committed == [created.data["id"]]

    async def test_a_failing_signal_receiver_rolls_the_save_back(self):
        def refuse(sender, **kwargs):
            raise RuntimeError("receiver")

        post_save.connect(refuse, sender=Author)
        self.addCleanup(post_save.disconnect, refuse, sender=Author)
        with pytest.raises(RuntimeError, match="receiver"):
            await self.api("post", "/authors/", data={"name": "Ursula"})
        assert not await Author.objects.aexists()

    async def test_a_transaction_of_the_project_around_the_save(self):
        created = await self.api("post", "/nested/", data={"name": "Ursula"})
        assert created.status_code == 201
        assert await Author.objects.filter(name="Ursula").aexists()

    async def test_many_to_many_rows_are_part_of_the_save(self):
        author = await Author.objects.acreate(name="Ursula")
        tag = await Tag.objects.acreate(name="sf")
        payload = {"title": "A", "isbn": "1", "author": author.pk, "tags": [tag.pk]}
        created = await self.api("post", "/books/", data=payload)
        assert created.status_code == 201, created.data
        assert (await self.api("get", "/books/")).data[0]["tags"] == [tag.pk]

    async def test_the_save_is_one_transaction_without_savepoints(self):
        # ATOMIC_SAVE's transaction is the only one: a second ``atomic``
        # inside it would cost a SAVEPOINT and a RELEASE per save.
        author = await Author.objects.acreate(name="Ursula")
        tag = await Tag.objects.acreate(name="sf")
        payload = {"title": "A", "isbn": "1", "author": author.pk, "tags": [tag.pk]}
        for isbn, url in enumerate(("/books/", "/aiodrf-books/")):
            with mock.patch.object(
                BaseDatabaseWrapper,
                "savepoint",
                autospec=True,
                side_effect=BaseDatabaseWrapper.savepoint,
            ) as savepoint:
                created = await self.api(
                    "post", url, data={**payload, "isbn": str(isbn)}
                )
            assert created.status_code == 201, created.data
            assert savepoint.call_count == 0, url


@override_settings(ROOT_URLCONF=__name__)
class TransactionASGITests(AsyncTransport, _TransactionTests, TransactionTestCase):
    pass


@override_settings(ROOT_URLCONF=__name__)
class TransactionWSGITests(SyncTransport, _TransactionTests, TransactionTestCase):
    pass
