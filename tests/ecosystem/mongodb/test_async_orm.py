"""
Django's async ORM in an ``async def`` handler on MongoDB. django-mongodb-backend
uses PyMongo's synchronous client; Django runs each query in a thread with
``sync_to_async``, not an aiodrf hop.
"""

from django.test import TestCase, override_settings
from django.urls import path

from aiodrf.response import Response
from aiodrf.test import AsyncAPIClient, count_hops
from aiodrf.views import APIView
from tests.ecosystem.mongodb.models import Author, Book


class Summary(APIView):
    async def get(self, request, pk):
        book = await Book.objects.select_related("author").aget(pk=pk)
        first = await Book.objects.order_by("title").afirst()
        return Response(
            {
                "count": await Book.objects.acount(),
                "first": str(first.pk),
                "author": book.author.name,
                "titles": [
                    title
                    async for title in Book.objects.order_by("title").values_list(
                        "title", flat=True
                    )
                ],
            }
        )


urlpatterns = [path("books/<str:pk>/", Summary.as_view())]


@override_settings(ROOT_URLCONF=__name__)
class AsyncORMTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        author = Author.objects.create(name="Ann")
        cls.books = [Book.objects.create(title=title, author=author) for title in "ab"]

    async def test_queries_without_a_hop(self):
        with count_hops() as hops:
            response = await AsyncAPIClient().get(f"/books/{self.books[1].pk}/")
        assert response.status_code == 200
        assert response.data == {
            "count": 2,
            "first": str(self.books[0].pk),
            "author": "Ann",
            "titles": ["a", "b"],
        }
        assert hops.count == 0, hops.calls
