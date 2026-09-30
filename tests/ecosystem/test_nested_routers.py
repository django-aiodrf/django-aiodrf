"""drf-nested-routers: aiodrf viewsets under a parent lookup."""

from django.test import override_settings
from django.urls import include, path
from rest_framework import viewsets as drf_viewsets
from rest_framework.permissions import AllowAny
from rest_framework.routers import SimpleRouter
from rest_framework_nested.routers import NestedSimpleRouter

from aiodrf import viewsets
from tests.base import both_transports
from tests.ecosystem.base import same_response
from tests.testapp.models import Author, Book
from tests.testapp.serializers import AuthorSerializer, BookSerializer


class Open:
    authentication_classes = []
    permission_classes = [AllowAny]


class BooksOfAuthor(Open):
    serializer_class = BookSerializer

    def get_queryset(self):
        return Book.objects.filter(author=self.kwargs["author_pk"]).order_by("pk")


def routes(viewset_base):
    authors = type(
        "Authors",
        (Open, viewset_base),
        {
            "queryset": Author.objects.order_by("pk"),
            "serializer_class": AuthorSerializer,
        },
    )
    books = type("Books", (BooksOfAuthor, viewset_base), {})
    router = SimpleRouter()
    router.register("authors", authors, basename="author")
    nested = NestedSimpleRouter(router, "authors", lookup="author")
    nested.register("books", books, basename="author-books")
    return [*router.urls, *nested.urls]


urlpatterns = [
    path("drf/", include((routes(drf_viewsets.ModelViewSet), "drf"))),
    path("aiodrf/", include((routes(viewsets.ModelViewSet), "aiodrf"))),
]


@both_transports
class _NestedRouterTests:
    @classmethod
    def setUpTestData(cls):
        cls.ursula, cls.other = (
            Author.objects.create(name=name) for name in ("Ursula", "Other")
        )
        Book.objects.create(title="A", isbn="1", author=cls.ursula)
        Book.objects.create(title="B", isbn="2", author=cls.other)

    @override_settings(ROOT_URLCONF=__name__)
    async def test_aiodrf_answers_like_drf(self):
        book = await Book.objects.aget(title="A")
        payload = {"title": "C", "isbn": "3", "author": self.ursula.pk, "tags": []}
        requests = [
            ("get", f"authors/{self.ursula.pk}/books/", None),
            ("get", f"authors/{self.ursula.pk}/books/{book.pk}/", None),
            # The book exists, under the other author.
            ("get", f"authors/{self.other.pk}/books/{book.pk}/", None),
            ("post", f"authors/{self.ursula.pk}/books/", payload),
        ]
        for method, url, data in requests:
            with self.subTest(method=method, url=url):
                aiodrf = await self.api(method, f"/aiodrf/{url}", data=data)
                if method == "post":
                    assert aiodrf.status_code == 201, aiodrf.data
                    continue
                drf = await self.api(method, f"/drf/{url}", data=data)
                assert same_response(drf, aiodrf), (drf.data, aiodrf.data)
        titles = [
            row["title"]
            for row in (
                await self.api("get", f"/aiodrf/authors/{self.ursula.pk}/books/")
            ).data
        ]
        assert titles == ["A", "C"]
