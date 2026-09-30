"""django-zeal raises on N+1 queries; ``Meta.auto_prefetch`` must not cause any."""

import pytest
from django.test import TestCase, override_settings
from django.urls import path
from rest_framework.permissions import AllowAny
from zeal import NPlusOneError, zeal_context

from aiodrf import viewsets
from aiodrf.test import AsyncAPIClient
from tests.testapp.models import Author, Book, Tag
from tests.testapp.serializers import NestedBookSerializer


class UnoptimizedSerializer(NestedBookSerializer):
    class Meta(NestedBookSerializer.Meta):
        auto_prefetch = False


class Books(viewsets.ReadOnlyModelViewSet):
    queryset = Book.objects.order_by("pk")
    serializer_class = NestedBookSerializer
    authentication_classes = []
    permission_classes = [AllowAny]


class UnoptimizedBooks(Books):
    serializer_class = UnoptimizedSerializer


urlpatterns = [
    path("books/", Books.as_view({"get": "list"})),
    path("unoptimized/", UnoptimizedBooks.as_view({"get": "list"})),
]


@override_settings(ROOT_URLCONF=__name__)
class ZealTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        tag = Tag.objects.create(name="sf")
        for index in range(4):
            author = Author.objects.create(name=f"Author {index}")
            Book.objects.create(
                title=f"Book {index}", isbn=str(index), author=author
            ).tags.add(tag)

    async def test_auto_prefetch_leaves_nothing_for_zeal_to_find(self):
        # zeal's context is a context variable: it follows the request into
        # aiodrf's worker thread.
        with zeal_context():
            response = await AsyncAPIClient().get("/books/")
        assert response.status_code == 200
        assert [book["author"]["name"] for book in response.data] == [
            f"Author {index}" for index in range(4)
        ]

    async def test_zeal_sees_the_queries_of_the_worker_thread(self):
        # Without this the test above would pass whatever aiodrf did.
        with zeal_context(), pytest.raises(NPlusOneError):
            await AsyncAPIClient().get("/unoptimized/")
