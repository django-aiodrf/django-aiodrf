"""
``ATOMIC_SAVE`` on MongoDB. django-mongodb-backend makes Django's
``transaction.atomic()`` a no-op; ``aiodrf.contrib.mongodb`` makes aiodrf
save in the backend's own ``transaction.atomic()``, on a replica set. A
standalone server has no transactions: the save keeps Django's no-op there.
"""

from unittest import mock

from django.core.checks import Tags, run_checks
from django.db import connections
from django.test import TestCase, override_settings
from django.urls import path
from django_mongodb_extensions.rest_framework import MongoModelSerializer
from rest_framework.exceptions import ValidationError

from aiodrf import viewsets
from aiodrf.aio import _save
from aiodrf.contrib.mongodb import apps
from aiodrf.contrib.mongodb.fields import ObjectIdPrimaryKeyRelatedField
from aiodrf.test import count_hops
from aiodrf.utils import run_sync
from tests.base import both_transports
from tests.ecosystem.mongodb.models import Author, Book, Note, Tag


class RefusedLate:
    """Refuses the object after ``create()`` wrote it (and its tags)."""

    def create(self, validated_data):
        super().create(validated_data)
        raise ValidationError({"late": ["refused"]})


class BookSerializer(RefusedLate, MongoModelSerializer):
    serializer_related_field = ObjectIdPrimaryKeyRelatedField

    class Meta:
        model = Book
        fields = ["id", "title", "author", "tags"]


class NoteSerializer(RefusedLate, MongoModelSerializer):
    class Meta:
        model = Note
        fields = ["id", "text"]


class Books(viewsets.ModelViewSet):
    queryset = Book.objects.all()
    serializer_class = BookSerializer


class Notes(viewsets.ModelViewSet):
    queryset = Note.objects.all()
    serializer_class = NoteSerializer


urlpatterns = [
    path("books/", Books.as_view({"post": "create"})),
    path("notes/", Notes.as_view({"post": "create"})),
]
urls = override_settings(ROOT_URLCONF=__name__)


@both_transports
class _AtomicSaveTests:
    databases = {"default", "standalone"}

    @classmethod
    def setUpTestData(cls):
        cls.author = Author.objects.create(name="Ann")
        cls.tag = Tag.objects.create(name="x")

    async def create_book(self):
        payload = {
            "title": "a",
            "author": str(self.author.pk),
            "tags": [str(self.tag.pk)],
        }
        with count_hops() as hops:
            response = await self.api("post", "/books/", data=payload)
        assert response.status_code == 400
        assert response.data == {"late": ["refused"]}
        if self.transport == "asgi":
            assert hops.count == 1, hops.calls
        return await Book.objects.acount(), await Book.tags.through.objects.acount()

    @urls
    async def test_a_late_refusal_leaves_nothing_on_a_replica_set(self):
        assert await self.create_book() == (0, 0)

    @urls
    async def test_without_the_contrib_the_writes_stay(self):
        with mock.patch.dict(_save._ATOMIC_FACTORIES, clear=True):
            assert await self.create_book() == (1, 1)

    @urls
    async def test_a_standalone_server_keeps_djangos_no_op(self):
        # Its transaction would fail with "Transaction numbers are only
        # allowed on a replica set member or mongos".
        response = await self.api("post", "/notes/", data={"text": "a"})
        assert response.status_code == 400
        assert await Note.objects.acount() == 1


class ContribTests(TestCase):
    databases = {"default", "standalone"}

    def test_the_app_registers_the_backends_transaction(self):
        assert _save._ATOMIC_FACTORIES["mongodb"] is apps.atomic

    async def test_the_backend_still_says_which_servers_have_transactions(self):
        # A private attribute of django-mongodb-backend: this fails loudly
        # when a release renames it.
        def supports_transactions(alias):
            return connections[alias].features._supports_transactions

        assert await run_sync(supports_transactions)("default") is True
        assert await run_sync(supports_transactions)("standalone") is False

    def test_the_check_asks_for_the_contrib(self):
        assert "aiodrf.W007" not in {
            m.id for m in run_checks(tags=[Tags.compatibility])
        }
        installed = [
            "rest_framework",
            "django_filters",
            "aiodrf",
            "tests.ecosystem.mongodb",
        ]
        with override_settings(INSTALLED_APPS=installed):
            warnings = [
                m
                for m in run_checks(tags=[Tags.compatibility])
                if m.id == "aiodrf.W007"
            ]
        assert [warning.obj for warning in warnings] == ["default", "standalone"]


def test_the_backend_says_whether_it_supports_transactions():
    # A private name aiodrf.contrib.mongodb reads
    # (docs/reference/upstream-internals.md).
    import inspect

    from django_mongodb_backend.features import DatabaseFeatures

    assert "_supports_transactions" in inspect.getsource(DatabaseFeatures)
