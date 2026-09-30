"""
MongoDB's fields in serializers: ``EmbeddedModelField`` and ``ArrayField``
through django-mongodb-extensions' ``MongoModelSerializer``, and relations to
``ObjectId`` keys. DRF's ``PrimaryKeyRelatedField`` represents such a key as
the ``ObjectId`` itself, which DRF's JSON encoder refuses; ``pk_field`` or
``aiodrf.contrib.mongodb``'s field represent it as a string.
"""

import pytest
from django.test import override_settings
from django.urls import path
from django_mongodb_extensions.rest_framework import MongoModelSerializer, ObjectIdField
from rest_framework import viewsets as drf_viewsets

from aiodrf import serializers, viewsets
from aiodrf.contrib.mongodb.fields import ObjectIdPrimaryKeyRelatedField
from aiodrf.test import count_hops
from tests.base import both_transports
from tests.ecosystem.mongodb.models import Address, Author, Book, Tag

FIELDS = ["id", "title", "author", "tags", "address", "keywords", "ref"]


class BookSerializer(MongoModelSerializer):
    class Meta:
        model = Book
        fields = FIELDS


class PkFieldBookSerializer(BookSerializer):
    class Meta(BookSerializer.Meta):
        extra_kwargs = {
            "author": {"pk_field": ObjectIdField()},
            "tags": {"pk_field": ObjectIdField()},
        }


class RelatedFieldBookSerializer(MongoModelSerializer, serializers.ModelSerializer):
    serializer_related_field = ObjectIdPrimaryKeyRelatedField

    class Meta:
        model = Book
        fields = FIELDS


SERIALIZERS = {
    "plain": BookSerializer,
    "pk-field": PkFieldBookSerializer,
    "related-field": RelatedFieldBookSerializer,
}
urlpatterns = []
for name, serializer_class in SERIALIZERS.items():
    for prefix, base in (
        ("drf", drf_viewsets.ModelViewSet),
        ("aiodrf", viewsets.ModelViewSet),
    ):
        viewset = type(
            f"{prefix}-{name}",
            (base,),
            {
                "queryset": Book.objects.order_by("title"),
                "serializer_class": serializer_class,
            },
        )
        urlpatterns += [
            path(
                f"{prefix}/{name}/", viewset.as_view({"get": "list", "post": "create"})
            ),
            path(
                f"{prefix}/{name}/<str:pk>/",
                viewset.as_view({"get": "retrieve", "patch": "partial_update"}),
            ),
        ]
urls = override_settings(ROOT_URLCONF=__name__)


def without_id(data):
    return {key: value for key, value in data.items() if key != "id"}


@both_transports
class _FieldTests:
    @classmethod
    def setUpTestData(cls):
        cls.author = Author.objects.create(name="Ann")
        cls.tags = [Tag.objects.create(name=name) for name in "xy"]

    def payload(self):
        return {
            "title": "a",
            "author": str(self.author.pk),
            "tags": [str(self.tags[0].pk)],
            "address": {"street": "Main", "city": "Oslo"},
            "keywords": ["k1", "k2"],
            "ref": str(self.author.pk),
        }

    # Embedded models and arrays are not compiled.
    @pytest.mark.aiodrf_settings(SERIALIZER_BACKEND_FALLBACK="drf")
    @urls
    async def test_embedded_array_and_relations_round_trip(self):
        for name in ("pk-field", "related-field"):
            with self.subTest(name=name):
                drf = await self.api("post", f"/drf/{name}/", data=self.payload())
                with count_hops() as hops:
                    created = await self.api(
                        "post", f"/aiodrf/{name}/", data=self.payload()
                    )
                assert created.status_code == drf.status_code == 201, created.data
                assert (
                    without_id(created.data) == without_id(drf.data) == self.payload()
                )
                if self.transport == "asgi":
                    assert hops.count == 1, hops.calls
                book = await Book.objects.aget(pk=created.data["id"])
                assert book.address.city == "Oslo"
                assert book.keywords == ["k1", "k2"]
                assert [tag.pk async for tag in book.tags.all()] == [self.tags[0].pk]

                change = {
                    "tags": [str(tag.pk) for tag in self.tags],
                    "address": {"street": "Side", "city": "Bergen"},
                    "keywords": [],
                }
                drf = await self.api(
                    "patch", f"/drf/{name}/{drf.data['id']}/", data=change
                )
                patched = await self.api(
                    "patch", f"/aiodrf/{name}/{book.pk}/", data=change
                )
                assert patched.status_code == drf.status_code == 200
                assert without_id(patched.data) == without_id(drf.data)
                assert without_id(patched.data) == {**self.payload(), **change}
                retrieved = await self.api("get", f"/aiodrf/{name}/{book.pk}/")
                assert retrieved.data == patched.data

    @urls
    async def test_nested_errors_are_drfs(self):
        payloads = [
            {**self.payload(), "address": {"street": "x" * 51}},
            {**self.payload(), "keywords": ["x" * 21]},
            {**self.payload(), "ref": "nope", "tags": ["nope"]},
        ]
        for payload in payloads:
            drf = await self.api("post", "/drf/related-field/", data=payload)
            response = await self.api("post", "/aiodrf/related-field/", data=payload)
            assert response.status_code == drf.status_code == 400
            assert response.data == drf.data
        assert response.data["ref"] == [
            "Enter a valid ObjectId (24-character hex string)."
        ]
        assert await Book.objects.acount() == 0

    # What DRF cannot represent is not compiled either.
    @pytest.mark.aiodrf_settings(SERIALIZER_BACKEND_FALLBACK="drf")
    @urls
    async def test_relations_without_a_string_key_fail_as_in_drf(self):
        book = await Book.objects.acreate(
            title="a", author=self.author, address=Address(street="s", city="c")
        )
        for prefix in ("drf", "aiodrf"):
            with (
                self.subTest(prefix=prefix),
                pytest.raises(
                    TypeError, match="Object of type ObjectId is not JSON serializable"
                ),
            ):
                await self.api("get", f"/{prefix}/plain/{book.pk}/")
