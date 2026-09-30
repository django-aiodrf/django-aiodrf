"""
django-restql: field selection with ``?query={...}``, query arguments that
become filter parameters, eager loading, and nested writes.

``DynamicFieldsMixin`` reads the query from ``context["request"]`` in the
serializer, in aiodrf's worker thread. ``QueryArgumentsMixin`` overrides the
view's ``dispatch`` synchronously and returns ``super().dispatch()``, which
is aiodrf's coroutine: the override stays valid. ``EagerLoadingMixin``
overrides ``get_queryset``. ``NestedModelSerializer`` writes the nested
objects in ``create``/``update`` (it does not override ``save``), so
``ATOMIC_SAVE`` makes the nested write one transaction.
"""

import warnings

import pytest
from django.test import TestCase, override_settings
from django.urls import include, path
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import serializers
from rest_framework import viewsets as drf_viewsets
from rest_framework.permissions import AllowAny
from rest_framework.routers import SimpleRouter
from rest_framework.test import APIClient

from aiodrf import viewsets
from aiodrf.test import AsyncAPIClient, count_hops
from tests.base import both_transports
from tests.ecosystem.base import same_response
from tests.testapp.models import Author, Book, Tag

# django-restql's parser, pypeg2 2.15, has invalid escape sequences: Python
# warns when it compiles the module, and warnings are errors here.
with warnings.catch_warnings():
    warnings.filterwarnings("ignore", ".* is an invalid escape sequence", SyntaxWarning)
    import pypeg2  # noqa: F401
from django_restql.fields import NestedField
from django_restql.mixins import (
    DynamicFieldsMixin,
    EagerLoadingMixin,
    QueryArgumentsMixin,
)
from django_restql.serializers import NestedModelSerializer

# django-restql's serializers define their own to_representation(), so its serializers
# cannot be compiled: the tests run them on DRF's code whatever fallback the run's
# profile sets.
pytestmark = pytest.mark.aiodrf_settings(SERIALIZER_BACKEND_FALLBACK="drf")


class RestqlAuthorSerializer(DynamicFieldsMixin, serializers.ModelSerializer):
    class Meta:
        model = Author
        fields = ["id", "name"]


class RestqlTagSerializer(DynamicFieldsMixin, serializers.ModelSerializer):
    class Meta:
        model = Tag
        fields = ["id", "name"]


class RestqlBookSerializer(DynamicFieldsMixin, NestedModelSerializer):
    author = NestedField(RestqlAuthorSerializer)
    tags = NestedField(RestqlTagSerializer, many=True, required=False)

    class Meta:
        model = Book
        fields = ["id", "title", "isbn", "author", "tags"]


def routes(base):
    router = SimpleRouter()
    router.register(
        "books",
        type(
            "Books",
            (QueryArgumentsMixin, EagerLoadingMixin, base),
            {
                "authentication_classes": [],
                "permission_classes": [AllowAny],
                "queryset": Book.objects.order_by("pk"),
                "serializer_class": RestqlBookSerializer,
                "filter_backends": [DjangoFilterBackend],
                "filterset_fields": ["title"],
                "select_related": {"author": "author"},
                "prefetch_related": {"tags": "tags"},
            },
        ),
        basename="book",
    )
    return router.urls


urlpatterns = [
    path("drf/", include((routes(drf_viewsets.ModelViewSet), "drf"))),
    path("aiodrf/", include((routes(viewsets.ModelViewSet), "aiodrf"))),
]
urls = override_settings(ROOT_URLCONF=__name__)

QUERIES = [
    "",
    "?query={id, title}",
    "?query={title, author{name}}",
    "?query={-isbn, -tags}",
    "?query={id, tags{name}}",
    '?query=(title: "B"){id, title}',
    "?query={id, unknown}",  # 400
    "?query={id",  # 400
]


class Books:
    @classmethod
    def setUpTestData(cls):
        cls.author = Author.objects.create(name="Ursula")
        cls.tags = [Tag.objects.create(name=name) for name in ("sea", "wizard")]
        for title in ("A", "B"):
            book = Book.objects.create(title=title, isbn=title, author=cls.author)
            book.tags.set(cls.tags)


@both_transports
class _RestqlTests(Books):
    @urls
    async def test_field_selection_and_arguments_answer_like_drf(self):
        book = await Book.objects.aget(title="A")
        for query in QUERIES:
            for url in ("books/", f"books/{book.pk}/"):
                with self.subTest(url=url, query=query):
                    drf = await self.api("get", f"/drf/{url}{query}")
                    aiodrf = await self.api("get", f"/aiodrf/{url}{query}")
                    assert same_response(drf, aiodrf), (drf.data, aiodrf.data)
        filtered = await self.api("get", '/aiodrf/books/?query=(title: "B"){title}')
        assert filtered.data == [{"title": "B"}]

    @urls
    async def test_nested_create_and_update_as_in_drf(self):
        results = {}
        for name in ("drf", "aiodrf"):
            payload = {
                "title": "C",
                "isbn": f"{name}-c",
                "author": {"name": f"{name}-Le Guin"},
                "tags": {"add": [self.tags[0].pk], "create": [{"name": f"{name}-new"}]},
            }
            created = await self.api("post", f"/{name}/books/", data=payload)
            assert created.status_code == 201, created.data
            changes = {"tags": {"remove": [self.tags[0].pk], "add": [self.tags[1].pk]}}
            updated = await self.api(
                "patch", f"/{name}/books/{created.data['id']}/", data=changes
            )
            assert updated.status_code == 200, updated.data
            results[name] = sorted(
                tag["name"].removeprefix(f"{name}-") for tag in updated.data["tags"]
            )
            assert updated.data["author"]["name"] == f"{name}-Le Guin"
        assert results["drf"] == results["aiodrf"] == ["new", "wizard"]


@pytest.mark.django_db(transaction=True)
async def test_a_selected_list_and_a_nested_create_cost_one_hop_each():
    author = await Author.objects.acreate(name="Ursula")
    await Book.objects.acreate(title="A", isbn="a", author=author)
    client = AsyncAPIClient()
    with override_settings(ROOT_URLCONF=__name__):
        with count_hops() as hops:
            listed = await client.get("/aiodrf/books/?query={title, author{name}}")
        assert listed.data == [{"title": "A", "author": {"name": "Ursula"}}]
        assert hops.calls == ["ListModelMixin._list"]
        with count_hops() as hops:
            created = await client.post(
                "/aiodrf/books/",
                {"title": "B", "isbn": "b", "author": {"name": "Vera"}},
                format="json",
            )
        assert created.status_code == 201, created.data
        assert hops.calls == ["CreateModelMixin._create"]


def duplicate_tags(prefix):
    # Each new tag is valid alone; restql creates them one by one after the
    # author and the book, and the second one's unique name refuses it (400).
    return {
        "title": "C",
        "isbn": prefix,
        "author": {"name": prefix},
        "tags": {"create": [{"name": f"{prefix}-x"}, {"name": f"{prefix}-x"}]},
    }


@both_transports
class _RestqlAtomicTests:
    @urls
    async def test_atomic_save_rolls_back_a_nested_create_refused_late(self):
        responses = {}
        for name in ("drf", "aiodrf"):
            responses[name] = await self.api(
                "post", f"/{name}/books/", data=duplicate_tags(name)
            )
        assert responses["drf"].status_code == responses["aiodrf"].status_code == 400
        assert repr(responses["drf"].data).replace("drf-", "") == repr(
            responses["aiodrf"].data
        ).replace("aiodrf-", "")
        # DRF without ATOMIC_REQUESTS keeps what was written before the error.
        assert await Book.objects.filter(isbn="drf", author__name="drf").aexists()
        assert await Tag.objects.filter(name="drf-x").aexists()
        assert not await Author.objects.filter(name="aiodrf").aexists()
        assert not await Book.objects.filter(isbn="aiodrf").aexists()
        assert not await Tag.objects.filter(name="aiodrf-x").aexists()


@urls
class RestqlEagerLoadingTests(Books, TestCase):
    """Query counts through WSGI: the worker thread is the test's thread."""

    def test_eager_loading_follows_the_query_as_in_drf(self):
        client = APIClient()
        for query in ("", "?query={id, title}", "?query={id, tags{name}}"):
            with self.subTest(query=query):
                with self.assertNumQueries(
                    {"": 2, "?query={id, title}": 1}.get(query, 2)
                ):
                    drf = client.get(f"/drf/books/{query}")
                with self.assertNumQueries(
                    {"": 2, "?query={id, title}": 1}.get(query, 2)
                ):
                    aiodrf = client.get(f"/aiodrf/books/{query}")
                assert drf.data == aiodrf.data
