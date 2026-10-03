"""
drf-flex-fields: ``?expand=``, ``?fields=`` and ``?omit=`` on aiodrf views.

The serializer reads the query parameters from ``context["request"]`` when
it is built, in aiodrf's worker thread, so the request must be DRF's there.
``FlexFieldsModelViewSet`` subclasses DRF's viewset; with aiodrf, combine
``FlexFieldsMixin`` with aiodrf's ``ModelViewSet`` instead.

``FlexFieldsFilterBackend`` does not import on DRF 3.18 (it imports
``coreapi`` from ``rest_framework.compat``, which DRF removed); that is
pinned below and is a fault of the package, not of aiodrf.
"""

import importlib

import pytest
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import include, path
from fastdrf.prefetch import forget_lookups
from rest_flex_fields import FlexFieldsModelSerializer
from rest_flex_fields.views import FlexFieldsMixin
from rest_framework import viewsets as drf_viewsets
from rest_framework.permissions import AllowAny
from rest_framework.routers import SimpleRouter
from rest_framework.test import APIClient

from aiodrf import viewsets
from aiodrf.test import AsyncAPIClient, count_hops
from tests.base import both_transports
from tests.ecosystem.base import same_response
from tests.testapp.models import Author, Book, Tag

# drf-flex-fields' serializers define their own to_representation(), so its serializers
# cannot be compiled: the tests run them on DRF's code whatever fallback the run's
# profile sets.
pytestmark = pytest.mark.aiodrf_settings(SERIALIZER_BACKEND_FALLBACK="drf")


class FlexAuthorSerializer(FlexFieldsModelSerializer):
    class Meta:
        model = Author
        fields = ["id", "name"]


class FlexTagSerializer(FlexFieldsModelSerializer):
    class Meta:
        model = Tag
        fields = ["id", "name"]


class FlexBookSerializer(FlexFieldsModelSerializer):
    class Meta:
        model = Book
        fields = ["id", "title", "isbn", "author", "tags"]
        expandable_fields = {
            "author": FlexAuthorSerializer,
            "tags": (FlexTagSerializer, {"many": True}),
        }


class FlexPrefetchedBookSerializer(FlexBookSerializer):
    class Meta(FlexBookSerializer.Meta):
        auto_prefetch = True


def viewset(base, serializer_class=FlexBookSerializer, **attrs):
    return type(
        "FlexBooks",
        (FlexFieldsMixin, base),
        {
            "authentication_classes": [],
            "permission_classes": [AllowAny],
            "queryset": Book.objects.order_by("pk"),
            "serializer_class": serializer_class,
            "permit_list_expands": ["author", "tags"],
            **attrs,
        },
    )


def routes(base, **attrs):
    router = SimpleRouter()
    router.register("books", viewset(base, **attrs), basename="book")
    return router.urls


PREFETCHED = {"serializer_class": FlexPrefetchedBookSerializer}
urlpatterns = [
    path("drf/", include((routes(drf_viewsets.ModelViewSet), "drf"))),
    path("aiodrf/", include((routes(viewsets.ModelViewSet), "aiodrf"))),
    path(
        "aiodrf-prefetched/",
        include((routes(viewsets.ModelViewSet, **PREFETCHED), "p")),
    ),
]
urls = override_settings(ROOT_URLCONF=__name__)

QUERIES = [
    "",
    "?expand=author",
    "?expand=author,tags",
    "?fields=id,title",
    "?omit=isbn,tags",
    "?fields=id,author&expand=author",
    "?expand=author&fields=author.name",
]


class Books:
    @classmethod
    def setUpTestData(cls):
        tags = [Tag.objects.create(name=name) for name in ("sea", "wizard")]
        for number in range(3):
            author = Author.objects.create(name=f"Author {number}")
            book = Book.objects.create(
                title=f"Book {number}", isbn=str(number), author=author
            )
            book.tags.set(tags)


@both_transports
class _FlexFieldsTests(Books):
    @urls
    async def test_list_and_retrieve_answer_like_drf(self):
        book = await Book.objects.afirst()
        for query in QUERIES:
            for url in ("books/", f"books/{book.pk}/"):
                with self.subTest(url=url, query=query):
                    drf = await self.api("get", f"/drf/{url}{query}")
                    aiodrf = await self.api("get", f"/aiodrf/{url}{query}")
                    assert drf.status_code == 200
                    assert same_response(drf, aiodrf), (drf.data, aiodrf.data)

    @urls
    async def test_writes_ignore_the_expansion_as_in_drf(self):
        payload = {
            "title": "New",
            "isbn": "{}",
            "author": (await Author.objects.afirst()).pk,
        }
        for name in ("drf", "aiodrf"):
            response = await self.api(
                "post", f"/{name}/books/?expand=author", data={**payload, "isbn": name}
            )
            assert response.status_code == 201, response.data
            assert response.data["author"]["name"] == "Author 0"


@pytest.mark.django_db(transaction=True)
async def test_an_expanded_list_costs_one_hop():
    author = await Author.objects.acreate(name="Ursula")
    await Book.objects.acreate(title="A", isbn="1", author=author)
    with count_hops() as hops, override_settings(ROOT_URLCONF=__name__):
        response = await AsyncAPIClient().get("/aiodrf/books/?expand=author,tags")
    assert response.status_code == 200
    assert response.data[0]["author"]["name"] == "Ursula"
    assert hops.calls == ["ListModelMixin._list"]


@urls
class FlexFieldsQueryTests(Books, TestCase):
    """Query counts through WSGI: the worker thread is the test's thread."""

    def setUp(self):
        forget_lookups()
        self.client = APIClient()

    def queries(self, url):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(url)
        assert response.status_code == 200, response.data
        return len(queries)

    def test_the_queries_are_drfs(self):
        for query in QUERIES:
            with self.subTest(query=query):
                assert self.queries(f"/aiodrf/books/{query}") == self.queries(
                    f"/drf/books/{query}"
                )

    def test_auto_prefetch_sees_the_fields_before_expansion(self):
        # drf-flex-fields expands in ``to_representation``; ``Meta.auto_prefetch``
        # derives its lookups from the fields before that. The tags (a PK list
        # unexpanded) are prefetched; an expanded author is still loaded per
        # book, as in DRF.
        assert self.queries("/aiodrf-prefetched/books/") == 2  # books, tags
        assert self.queries("/drf/books/") == 4  # books, tags per book
        assert self.queries("/aiodrf-prefetched/books/?expand=author,tags") == 2 + 3
        assert self.queries("/drf/books/?expand=author,tags") == 1 + 3 + 3
        response = self.client.get("/aiodrf-prefetched/books/?expand=author,tags")
        assert response.data[0]["author"] == {
            "id": response.data[0]["author"]["id"],
            "name": "Author 0",
        }
        assert [tag["name"] for tag in response.data[0]["tags"]] == ["sea", "wizard"]


def test_the_filter_backend_does_not_import_on_drf_3_18():
    with pytest.raises(ImportError, match="coreapi"):
        importlib.import_module("rest_flex_fields.filter_backends")
