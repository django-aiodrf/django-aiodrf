"""
The async validation path must produce exactly what DRF produces.

Each case validates the same input twice: with DRF's synchronous
``is_valid()`` and with aiodrf's async walker, which is forced by giving the
serializer an async hook that does nothing. ``errors`` and
``validated_data`` must be identical.
"""

import json

from asgiref.sync import sync_to_async
from django.test import TestCase
from drf_spectacular.generators import SchemaGenerator
from rest_framework import serializers as drf_serializers
from rest_framework import viewsets as drf_viewsets
from rest_framework.routers import SimpleRouter

from aiodrf import aio, viewsets
from aiodrf.aio import _classify
from tests.testapp.models import Author, Book, Tag
from tests.testapp.serializers import BookSerializer


def with_async_hook(serializer_class):
    """Subclass whose ``validate`` is async, forcing aiodrf's async walker."""

    async def validate(self, attrs):
        return attrs

    return type(
        f"Async{serializer_class.__name__}", (serializer_class,), {"validate": validate}
    )


class NestedAuthorSerializer(drf_serializers.Serializer):
    author = drf_serializers.PrimaryKeyRelatedField(queryset=Author.objects.all())
    books = BookSerializer(many=True)
    note = drf_serializers.CharField(max_length=5, required=False)


class ParityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.author = Author.objects.create(name="Ursula")
        cls.tag = Tag.objects.create(name="sf")
        Book.objects.create(title="Taken", isbn="0000000000001", author=cls.author)

    async def assert_parity(self, serializer_class, data, **kwargs):
        sync = serializer_class(data=data, **kwargs)
        await sync_to_async(sync.is_valid)()

        async_class = with_async_hook(serializer_class)
        asynchronous = async_class(data=data, **kwargs)
        assert _classify.plan_for(asynchronous).validation is _classify.Kind.ASYNC
        await aio.is_valid(asynchronous)

        assert _plain(asynchronous.errors) == _plain(sync.errors)
        assert asynchronous.validated_data == sync.validated_data
        if sync.errors:
            codes = _codes(sync.errors)
            assert _codes(asynchronous.errors) == codes

    async def test_valid(self):
        data = {
            "title": "New",
            "isbn": "0000000000002",
            "author": self.author.pk,
            "tags": [self.tag.pk],
        }
        await self.assert_parity(BookSerializer, data)

    async def test_relations_unique_and_required(self):
        data = {
            "title": "",
            "isbn": "0000000000001",
            "author": 999,
            "tags": [self.tag.pk, 999],
        }
        await self.assert_parity(BookSerializer, data)
        await self.assert_parity(BookSerializer, {})
        await self.assert_parity(BookSerializer, "not a dict")
        await self.assert_parity(
            BookSerializer, {"author": "x", "tags": "x", "pages": -1}
        )

    async def test_partial(self):
        await self.assert_parity(BookSerializer, {"pages": "x"}, partial=True)
        await self.assert_parity(BookSerializer, {"pages": 3}, partial=True)

    async def test_nested_and_many(self):
        data = {
            "author": self.author.pk,
            "books": [
                {
                    "title": "A",
                    "isbn": "0000000000003",
                    "author": self.author.pk,
                    "tags": [],
                },
                {"title": "B", "isbn": "0000000000001", "author": 999, "tags": []},
            ],
            "note": "too long",
        }
        await self.assert_parity(NestedAuthorSerializer, data)
        await self.assert_parity(NestedAuthorSerializer, {"author": None, "books": "x"})

    async def test_many_true(self):
        data = [
            {
                "title": "A",
                "isbn": "0000000000004",
                "author": self.author.pk,
                "tags": [],
            },
            {
                "title": "",
                "isbn": "0000000000001",
                "author": self.author.pk,
                "tags": [],
            },
        ]
        await self.assert_parity(BookSerializer, data, many=True)
        await self.assert_parity(BookSerializer, {"not": "a list"}, many=True)


def _plain(errors):
    return json.loads(json.dumps(errors))


def _codes(errors):
    if isinstance(errors, dict):
        return {str(key): _codes(value) for key, value in errors.items()}
    if isinstance(errors, list):
        return [_codes(item) for item in errors]
    return getattr(errors, "code", None)


# -- OpenAPI parity -------------------------------------------------------------------


class DRFBookViewSet(drf_viewsets.ModelViewSet):
    queryset = Book.objects.all()
    serializer_class = BookSerializer


class AioBookViewSet(viewsets.ModelViewSet):
    queryset = Book.objects.all()
    serializer_class = BookSerializer


def test_schema_matches_drf():
    def schema_for(viewset):
        router = SimpleRouter()
        router.register("books", viewset, basename="book")
        return SchemaGenerator(patterns=router.urls).get_schema(
            request=None, public=True
        )

    drf_schema = schema_for(DRFBookViewSet)
    aio_schema = schema_for(AioBookViewSet)
    assert aio_schema["paths"] == drf_schema["paths"]
    assert aio_schema["components"] == drf_schema["components"]
