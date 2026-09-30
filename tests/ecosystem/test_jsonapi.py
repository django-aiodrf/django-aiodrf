"""
djangorestframework-jsonapi: its renderer, parser, pagination, filter
backends, metadata and exception handler, ``?include=`` through
``included_serializers`` and its ``get_queryset`` mixins
(``AutoPrefetchMixin``, ``PreloadIncludesMixin``) on an aiodrf viewset.
"""

import json

import pytest
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import include, path
from rest_framework.permissions import AllowAny
from rest_framework.routers import SimpleRouter
from rest_framework_json_api import filters, serializers
from rest_framework_json_api import views as jsonapi_views
from rest_framework_json_api.django_filters import DjangoFilterBackend
from rest_framework_json_api.metadata import JSONAPIMetadata
from rest_framework_json_api.pagination import JsonApiPageNumberPagination
from rest_framework_json_api.parsers import JSONParser
from rest_framework_json_api.renderers import JSONRenderer

from aiodrf import viewsets
from aiodrf.test import APIClient, AsyncAPIClient, count_hops
from tests.base import both_transports
from tests.testapp.models import Author, Book, Tag

# djangorestframework-jsonapi's ResourceRelatedField is not DRF's, so its serializers
# cannot be compiled: the tests run them on DRF's code whatever fallback the run's
# profile sets.
pytestmark = pytest.mark.aiodrf_settings(SERIALIZER_BACKEND_FALLBACK="drf")

MEDIA_TYPE = "application/vnd.api+json"


class AuthorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Author
        fields = ["name"]


class TagSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tag
        fields = ["name"]


class BookSerializer(serializers.ModelSerializer):
    included_serializers = {"author": AuthorSerializer, "tags": TagSerializer}

    class Meta:
        model = Book
        fields = ["title", "isbn", "pages", "author", "tags"]


class Pages(JsonApiPageNumberPagination):
    page_size = 2


class Policies:
    queryset = Book.objects.order_by("pk")
    serializer_class = BookSerializer
    authentication_classes = []
    permission_classes = [AllowAny]
    renderer_classes = [JSONRenderer]
    parser_classes = [JSONParser]
    metadata_class = JSONAPIMetadata
    pagination_class = Pages
    filter_backends = [
        filters.QueryParameterValidationFilter,
        filters.OrderingFilter,
        DjangoFilterBackend,
    ]
    filterset_fields = {"pages": ("exact", "gte")}
    ordering_fields = ["title", "pages"]
    select_for_includes = {"author": ["author"]}


class DRFBooks(Policies, jsonapi_views.ModelViewSet):
    pass


class Books(
    Policies,
    jsonapi_views.AutoPrefetchMixin,
    jsonapi_views.PreloadIncludesMixin,
    jsonapi_views.RelatedMixin,
    viewsets.ModelViewSet,
):
    http_method_names = jsonapi_views.ModelViewSet.http_method_names


drf_router, router = SimpleRouter(), SimpleRouter()
drf_router.register("books", DRFBooks, basename="drf-book")
router.register("books", Books, basename="book")
urlpatterns = [
    path("drf/", include(drf_router.urls)),
    path("aiodrf/", include(router.urls)),
]

jsonapi = override_settings(
    ROOT_URLCONF=__name__,
    REST_FRAMEWORK={
        "EXCEPTION_HANDLER": "rest_framework_json_api.exceptions.exception_handler",
        "TEST_REQUEST_DEFAULT_FORMAT": "json",
    },
)


def body(response, prefix):
    """The document with the view's URL prefix taken out of its links."""
    return response.content.replace(f"/{prefix}/".encode(), b"/<prefix>/")


class Fixtures:
    @classmethod
    def setUpTestData(cls):
        ursula = Author.objects.create(name="Ursula")
        octavia = Author.objects.create(name="Octavia")
        fantasy = Tag.objects.create(name="fantasy")
        for number, (title, author, pages) in enumerate(
            [
                ("Earthsea", ursula, 180),
                ("Kindred", octavia, 260),
                ("Dispossessed", ursula, 380),
            ]
        ):
            book = Book.objects.create(
                title=title, isbn=f"97800000000{number}", pages=pages, author=author
            )
            book.tags.add(fantasy)


@both_transports
class _JsonApiTests(Fixtures):
    async def same(self, url):
        drf = await self.api("get", f"/drf/{url}")
        aiodrf = await self.api("get", f"/aiodrf/{url}")
        assert aiodrf.status_code == drf.status_code
        assert aiodrf["content-type"] == drf["content-type"]
        assert body(aiodrf, "aiodrf") == body(drf, "drf")
        return aiodrf

    @jsonapi
    async def test_list_with_pagination_sorting_filters_and_include(self):
        response = await self.same(
            "books/?include=author,tags&sort=-pages&filter[pages.gte]=200"
        )
        assert response.status_code == 200
        assert response["content-type"] == MEDIA_TYPE
        document = response.json()
        assert [item["attributes"]["title"] for item in document["data"]] == [
            "Dispossessed",
            "Kindred",
        ]
        assert sorted(item["type"] for item in document["included"]) == [
            "Author",
            "Author",
            "Tag",
        ]
        assert document["meta"]["pagination"]["count"] == 2

    @jsonapi
    async def test_retrieve_with_include_and_the_second_page(self):
        book = await Book.objects.aget(title="Kindred")
        await self.same(f"books/{book.pk}/?include=author")
        response = await self.same("books/?page[number]=2")
        assert [item["attributes"]["title"] for item in response.json()["data"]] == [
            "Dispossessed"
        ]

    @jsonapi
    async def test_errors_are_json_api_error_objects(self):
        invalid = await self.same("books/?unknown=1")
        assert invalid.status_code == 400
        assert (
            invalid.json()["errors"][0]["detail"] == "invalid query parameter: unknown"
        )
        missing = await self.same("books/999/")
        assert missing.status_code == 404
        assert missing.json()["errors"][0]["status"] == "404"
        await self.same("books/?sort=isbn")

    @jsonapi
    async def test_create_parses_the_resource_object(self):
        author = await Author.objects.aget(name="Octavia")
        responses = {}
        for number, prefix in enumerate(("drf", "aiodrf")):
            document = {
                "data": {
                    "type": "Book",
                    "attributes": {
                        "title": "Fledgling",
                        "isbn": f"97811111111{number}",
                    },
                    "relationships": {
                        "author": {"data": {"type": "Author", "id": str(author.pk)}}
                    },
                }
            }
            responses[prefix] = await self.api(
                "post",
                f"/{prefix}/books/",
                data=json.dumps(document),
                content_type=MEDIA_TYPE,
            )
        drf, aiodrf = responses["drf"].json(), responses["aiodrf"].json()
        assert responses["aiodrf"].status_code == responses["drf"].status_code == 201
        for document in (drf, aiodrf):
            del document["data"]["id"], document["data"]["attributes"]["isbn"]
            document["data"]["links"] = None
        assert aiodrf == drf
        wrong_type = await self.api(
            "post",
            "/aiodrf/books/",
            data=json.dumps({"data": {"type": "Author", "attributes": {"title": "x"}}}),
            content_type=MEDIA_TYPE,
        )
        assert wrong_type.status_code == 409

    @jsonapi
    async def test_options_use_the_json_api_metadata(self):
        drf = await self.api("options", "/drf/books/")
        aiodrf = await self.api("options", "/aiodrf/books/")
        assert aiodrf.status_code == 200
        drf, aiodrf = drf.json(), aiodrf.json()
        # The name comes from the class name.
        assert (drf["data"].pop("name"), aiodrf["data"].pop("name")) == (
            "Drf Books List",
            "Books List",
        )
        assert aiodrf == drf


class JsonApiCostTests(Fixtures, TestCase):
    @jsonapi
    async def test_the_list_is_one_hop(self):
        with count_hops() as hops:
            response = await AsyncAPIClient().get("/aiodrf/books/?include=author,tags")
        assert response.status_code == 200
        # The mixins' ``get_queryset`` is user code; it runs in the list's hop.
        assert hops.calls == ["ListModelMixin._list"]

    @jsonapi
    def test_the_include_mixins_prefetch_as_in_drf(self):
        # Through WSGI, where the queries run on this thread's connection.
        client = APIClient()
        with CaptureQueriesContext(connection) as drf:
            client.get("/drf/books/?include=author,tags")
        with CaptureQueriesContext(connection) as aiodrf:
            client.get("/aiodrf/books/?include=author,tags")
        # Count, page with the author joined, tags prefetched: no query per book.
        assert [q["sql"] for q in aiodrf] == [q["sql"] for q in drf]
        assert len(aiodrf) == 3
