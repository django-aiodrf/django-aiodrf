"""
django-mongodb-backend under aiodrf's generic views. Every key is an
``ObjectId``: in URLs, in foreign keys, in filters. DRF maps
``ObjectIdAutoField`` like ``AutoField``, to an ``IntegerField``, which
cannot represent one; django-mongodb-extensions' ``MongoModelSerializer``
maps it to a string field. Both views answer the same, and the aiodrf one in
one hop per request.
"""

import pytest
from django.test import override_settings
from django.urls import path
from django_mongodb_extensions.rest_framework import MongoModelSerializer
from rest_framework import serializers as drf_serializers
from rest_framework import viewsets as drf_viewsets
from rest_framework.pagination import PageNumberPagination

from aiodrf import serializers, viewsets
from aiodrf.contrib.django_filters import DjangoFilterBackend
from aiodrf.contrib.mongodb.fields import ObjectIdPrimaryKeyRelatedField
from aiodrf.test import count_hops
from tests.base import both_transports
from tests.ecosystem.mongodb.models import Author, Book


class BookSerializer(MongoModelSerializer):
    serializer_related_field = ObjectIdPrimaryKeyRelatedField

    class Meta:
        model = Book
        fields = ["id", "title", "pages", "author"]


class MixedBookSerializer(MongoModelSerializer, serializers.ModelSerializer):
    """django-mongodb-extensions' field mapping on aiodrf's serializer."""

    serializer_related_field = ObjectIdPrimaryKeyRelatedField

    class Meta:
        model = Book
        fields = ["id", "title", "pages", "author"]


class PlainBookSerializer(drf_serializers.ModelSerializer):
    class Meta:
        model = Book
        fields = ["id", "title"]


class Pages(PageNumberPagination):
    page_size = 2


class Policies:
    queryset = Book.objects.order_by("title")
    serializer_class = BookSerializer
    pagination_class = Pages
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["title", "author"]


class DRFBooks(Policies, drf_viewsets.ModelViewSet):
    pass


class Books(Policies, viewsets.ModelViewSet):
    pass


class MixedBooks(Books):
    serializer_class = MixedBookSerializer


class DRFPlainBooks(DRFBooks):
    serializer_class = PlainBookSerializer


class PlainBooks(Books):
    serializer_class = PlainBookSerializer


VIEWS = {
    "drf": DRFBooks,
    "aiodrf": Books,
    "mixed": MixedBooks,
    "drf-plain": DRFPlainBooks,
    "aiodrf-plain": PlainBooks,
}
urlpatterns = [
    route
    for prefix, viewset in VIEWS.items()
    for route in (
        path(f"{prefix}/", viewset.as_view({"get": "list", "post": "create"})),
        path(
            f"{prefix}/<str:pk>/",
            viewset.as_view(
                {
                    "get": "retrieve",
                    "put": "update",
                    "patch": "partial_update",
                    "delete": "destroy",
                }
            ),
        ),
    )
]
urls = override_settings(ROOT_URLCONF=__name__)


def without_id(data):
    return {key: value for key, value in data.items() if key != "id"}


@both_transports
class _ViewTests:
    @classmethod
    def setUpTestData(cls):
        cls.ann = Author.objects.create(name="Ann")
        cls.bob = Author.objects.create(name="Bob")
        cls.books = [
            Book.objects.create(title=title, author=author)
            for title, author in (("a", cls.ann), ("b", cls.bob), ("c", cls.ann))
        ]

    async def one_hop(self, method, url, **kwargs):
        with count_hops() as hops:
            response = await self.api(method, url, **kwargs)
        if self.transport == "asgi":
            assert hops.count == 1, hops.calls
        return response

    @urls
    async def test_pages_and_filters(self):
        for query in ("", "?page=2", f"?author={self.ann.pk}", "?title=b"):
            drf = await self.api("get", f"/drf/{query}")
            for prefix in ("aiodrf", "mixed"):
                with self.subTest(prefix=prefix, query=query):
                    response = await self.one_hop("get", f"/{prefix}/{query}")
                    assert response.status_code == drf.status_code == 200
                    assert response.data["count"] == drf.data["count"]
                    assert response.data["results"] == drf.data["results"]
        response = await self.api("get", "/aiodrf/")
        assert response.data["count"] == 3
        assert response.data["results"][0] == {
            "id": str(self.books[0].pk),
            "title": "a",
            "pages": 0,
            "author": str(self.ann.pk),
        }

    @urls
    async def test_an_invalid_filter_is_drfs_400(self):
        drf = await self.api("get", "/drf/?author=nope")
        response = await self.one_hop("get", "/aiodrf/?author=nope")
        assert response.status_code == drf.status_code == 400
        assert response.data == drf.data

    @urls
    async def test_create_retrieve_update_delete(self):
        for prefix in ("drf", "aiodrf", "mixed"):
            with self.subTest(prefix=prefix):
                call = self.api if prefix == "drf" else self.one_hop
                created = await call(
                    "post",
                    f"/{prefix}/",
                    data={"title": "d", "author": str(self.bob.pk)},
                )
                assert created.status_code == 201, created.data
                pk = created.data["id"]
                assert without_id(created.data) == {
                    "title": "d",
                    "pages": 0,
                    "author": str(self.bob.pk),
                }
                retrieved = await call("get", f"/{prefix}/{pk}/")
                assert retrieved.data == created.data
                patched = await call("patch", f"/{prefix}/{pk}/", data={"pages": 5})
                assert patched.data["pages"] == 5
                put = await call(
                    "put",
                    f"/{prefix}/{pk}/",
                    data={"title": "e", "author": str(self.ann.pk)},
                )
                assert without_id(put.data) == {
                    "title": "e",
                    "pages": 5,
                    "author": str(self.ann.pk),
                }
                assert (await call("delete", f"/{prefix}/{pk}/")).status_code == 204
                assert not await Book.objects.filter(pk=pk).aexists()

    @urls
    async def test_malformed_and_unknown_keys_are_404(self):
        for pk in ("nope", "0" * 24):
            drf = await self.api("get", f"/drf/{pk}/")
            response = await self.one_hop("get", f"/aiodrf/{pk}/")
            assert response.status_code == drf.status_code == 404
            assert response.data == drf.data

    @urls
    async def test_an_invalid_foreign_key_is_drfs_400(self):
        for author in ("nope", "0" * 24):
            payload = {"title": "d", "author": author}
            drf = await self.api("post", "/drf/", data=payload)
            response = await self.one_hop("post", "/aiodrf/", data=payload)
            assert response.status_code == drf.status_code == 400
            assert response.data == drf.data
        assert await Book.objects.acount() == 3

    # What DRF cannot represent is not compiled either.
    @pytest.mark.aiodrf_settings(SERIALIZER_BACKEND_FALLBACK="drf")
    @urls
    async def test_a_plain_model_serializer_fails_as_in_drf(self):
        # DRF's ``IntegerField`` for the key calls ``int(ObjectId)``.
        for prefix in ("drf-plain", "aiodrf-plain"):
            with (
                self.subTest(prefix=prefix),
                pytest.raises(TypeError, match="not 'ObjectId'"),
            ):
                await self.api("get", f"/{prefix}/{self.books[0].pk}/")
