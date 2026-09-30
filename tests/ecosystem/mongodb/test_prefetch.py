"""
``Meta.auto_prefetch`` on MongoDB: django-mongodb-backend joins a foreign
key with ``$lookup`` (``select_related``) and prefetches a reverse foreign
key, but refuses ``prefetch_related`` of a many-to-many relation. aiodrf
leaves those out; they are read per object, as without ``auto_prefetch``.
"""

from django.db import NotSupportedError
from django.test import TestCase, override_settings
from django.urls import path
from django_mongodb_extensions.rest_framework import MongoModelSerializer
from rest_framework import viewsets as drf_viewsets

from aiodrf import serializers, viewsets
from aiodrf.contrib.mongodb.fields import ObjectIdPrimaryKeyRelatedField
from aiodrf.test import APIClient, AsyncAPIClient, count_hops
from tests.ecosystem.mongodb.models import Author, Book, Tag


class Serializer(MongoModelSerializer, serializers.ModelSerializer):
    serializer_related_field = ObjectIdPrimaryKeyRelatedField


class NameSerializer(Serializer):
    class Meta:
        model = Author
        fields = ["id", "name"]


class BookSerializer(Serializer):
    author = NameSerializer()

    class Meta:
        model = Book
        fields = ["id", "title", "author", "tags"]
        auto_prefetch = True


class AuthorSerializer(Serializer):
    books = BookSerializer(many=True)

    class Meta:
        model = Author
        fields = ["id", "name", "books"]
        auto_prefetch = True


def viewset(base, model, serializer_class):
    return type(
        model.__name__,
        (base,),
        {
            "queryset": model.objects.order_by("name" if model is Author else "title"),
            "serializer_class": serializer_class,
        },
    )


urlpatterns = [
    path(
        f"{prefix}/{model.__name__.lower()}s/",
        viewset(base, model, serializer_class).as_view({"get": "list"}),
    )
    for prefix, base in (
        ("drf", drf_viewsets.ModelViewSet),
        ("aiodrf", viewsets.ModelViewSet),
    )
    for model, serializer_class in ((Book, BookSerializer), (Author, AuthorSerializer))
]


@override_settings(ROOT_URLCONF=__name__)
class AutoPrefetchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        tags = [Tag.objects.create(name=name) for name in "xy"]
        for name in ("Ann", "Bob"):
            author = Author.objects.create(name=name)
            for title in ("a", "b"):
                book = Book.objects.create(title=f"{name}-{title}", author=author)
                book.tags.set(tags)

    def test_the_backend_refuses_to_prefetch_a_many_to_many_relation(self):
        with self.assertRaisesMessage(
            NotSupportedError, "prefetch_related() is not supported"
        ):
            list(Book.objects.prefetch_related("tags"))

    def test_a_foreign_key_is_joined_and_tags_are_read_per_book(self):
        client = APIClient()
        with self.assertNumQueries(1 + 4):  # books with their author, then tags
            response = client.get("/aiodrf/books/")
        assert response.status_code == 200
        assert response.data == client.get("/drf/books/").data
        assert response.data[0]["author"]["name"] == "Ann"
        assert len(response.data[0]["tags"]) == 2

    def test_a_reverse_foreign_key_is_prefetched(self):
        client = APIClient()
        # Authors, their books with each book's author, then tags per book.
        with self.assertNumQueries(2 + 4):
            response = client.get("/aiodrf/authors/")
        assert response.status_code == 200
        assert response.data == client.get("/drf/authors/").data

    async def test_one_hop(self):
        for url in ("/aiodrf/books/", "/aiodrf/authors/"):
            with count_hops() as hops:
                response = await AsyncAPIClient().get(url)
            assert response.status_code == 200
            assert hops.count == 1, hops.calls
