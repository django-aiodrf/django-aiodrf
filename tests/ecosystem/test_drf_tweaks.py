"""
drf-tweaks: ``ApiVersionMixin`` (a ``get_serializer_class`` per version,
deprecated and obsolete versions) with its ``DeprecationMiddleware``,
``AutoOptimizeMixin`` (a ``get_queryset`` that joins what the serializer
reads), ``@autofilter`` and ``NoCountsLimitOffsetPagination``.
"""

import pytest
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import path
from drf_tweaks import serializers
from drf_tweaks.autofilter import autofilter
from drf_tweaks.optimizator import AutoOptimizeMixin
from drf_tweaks.pagination import NoCountsLimitOffsetPagination
from drf_tweaks.versioning import ApiVersionMixin
from rest_framework import generics as drf_generics
from rest_framework.permissions import AllowAny
from rest_framework.versioning import QueryParameterVersioning

from aiodrf import generics
from aiodrf.test import APIClient, AsyncAPIClient, count_hops
from tests.base import both_transports
from tests.testapp.models import Author, Book

# drf-tweaks' versioned serializers define their own to_representation(), so its
# serializers cannot be compiled: the tests run them on DRF's code whatever fallback the
# run's profile sets.
pytestmark = pytest.mark.aiodrf_settings(SERIALIZER_BACKEND_FALLBACK="drf")


class AuthorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Author
        fields = ["name"]


class BookV1(serializers.ModelSerializer):
    class Meta:
        model = Book
        fields = ["id", "title"]


class BookV2(serializers.ModelSerializer):
    author = AuthorSerializer()

    class Meta:
        model = Book
        fields = ["id", "title", "isbn", "author"]


class BookV3(BookV2):
    class Meta(BookV2.Meta):
        fields = [*BookV2.Meta.fields, "pages"]


class Pages(NoCountsLimitOffsetPagination):
    default_limit = 2


class Policies:
    queryset = Book.objects.order_by("pk")
    authentication_classes = []
    permission_classes = [AllowAny]
    serializer_class = BookV3
    pagination_class = Pages


class Versioned(Policies):
    versioning_class = QueryParameterVersioning
    versioning_serializer_classess = {1: BookV1, 2: BookV2, 3: BookV3}
    CUSTOM_DEPRECATED_VERSION = 2
    CUSTOM_OBSOLETE_VERSION = 1


class DRFBooks(ApiVersionMixin, AutoOptimizeMixin, Versioned, drf_generics.ListAPIView):
    pass


class Books(ApiVersionMixin, AutoOptimizeMixin, Versioned, generics.ListAPIView):
    pass


# ``@autofilter`` instantiates the view without a request, which
# ``ApiVersionMixin.get_serializer_class`` cannot answer (in DRF too).
@autofilter()
class DRFFiltered(Policies, drf_generics.ListAPIView):
    pass


@autofilter()
class Filtered(Policies, generics.ListAPIView):
    pass


urlpatterns = [
    path("drf/", DRFBooks.as_view()),
    path("aiodrf/", Books.as_view()),
    path("drf/filtered/", DRFFiltered.as_view()),
    path("aiodrf/filtered/", Filtered.as_view()),
]
tweaks = override_settings(
    ROOT_URLCONF=__name__, MIDDLEWARE=["drf_tweaks.versioning.DeprecationMiddleware"]
)


class Fixtures:
    @classmethod
    def setUpTestData(cls):
        ursula = Author.objects.create(name="Ursula")
        octavia = Author.objects.create(name="Octavia")
        for number, (title, author) in enumerate(
            [("Earthsea", ursula), ("Kindred", octavia), ("Dispossessed", ursula)]
        ):
            Book.objects.create(title=title, isbn=f"97800000000{number}", author=author)


@both_transports
class _TweaksTests(Fixtures):
    async def same(self, query):
        drf = await self.api("get", f"/drf/{query}")
        aiodrf = await self.api("get", f"/aiodrf/{query}")
        assert aiodrf.status_code == drf.status_code
        assert aiodrf.content == drf.content.replace(b"/drf/", b"/aiodrf/")
        assert aiodrf.get("Warning") == drf.get("Warning")
        return aiodrf

    @tweaks
    async def test_the_version_picks_the_serializer(self):
        current = await self.same("?version=3")
        assert current.status_code == 200
        assert "Warning" not in current
        assert list(current.json()["results"][0]) == [
            "id",
            "title",
            "isbn",
            "author",
            "pages",
        ]

    @tweaks
    async def test_deprecated_obsolete_and_unknown_versions(self):
        # The mixin marks the Django request from inside ``get_serializer_class``;
        # the middleware reads the mark on the way out.
        deprecated = await self.same("?version=2")
        assert deprecated.status_code == 200
        assert deprecated["Warning"] == '299 - "This Api Version is Deprecated"'
        assert (await self.same("?version=1")).status_code == 410
        assert (await self.same("?version=9")).status_code == 400
        assert (await self.same("?version=x")).status_code == 400

    @tweaks
    async def test_pagination_without_counts_and_the_autofilter(self):
        first = await self.same("?version=3")
        assert set(first.json()) == {"next", "previous", "results"}
        assert first.json()["next"].endswith("?limit=2&offset=2&version=3")
        last = await self.same("?version=3&offset=2")
        assert last.json()["next"] is None
        # ``@autofilter`` filters and orders by indexed fields (``isbn`` is unique).
        filtered = await self.same(
            "filtered/?isbn__in=978000000000,978000000001&ordering=-id"
        )
        assert [book["title"] for book in filtered.json()["results"]] == [
            "Kindred",
            "Earthsea",
        ]
        # ``title`` has no index: not a filter, the parameter is ignored.
        unfiltered = await self.same("filtered/?title=Kindred")
        assert len(unfiltered.json()["results"]) == 2
        # A negative offset shortens the page; nothing left is an error.
        assert (await self.same("?version=3&offset=-5")).status_code == 400


@tweaks
class TweaksCostTests(Fixtures, TestCase):
    async def test_the_list_costs_one_hop(self):
        # ``get_serializer_class`` and ``get_queryset`` are user code; both run
        # in the list's hop, where aiodrf also asks for the serializer class.
        # The paginated response is an ``OrderedDict``, rendered on the loop
        # like a ``dict``.
        with count_hops() as hops:
            response = await AsyncAPIClient().get("/aiodrf/?version=2")
        assert response.status_code == 200
        assert hops.calls == ["ListModelMixin._list"]

    def test_auto_optimize_queries_as_in_drf(self):
        # Through WSGI, where the queries run on this thread's connection.
        client = APIClient()
        with CaptureQueriesContext(connection) as drf:
            client.get("/drf/?version=3")
        with CaptureQueriesContext(connection) as aiodrf:
            client.get("/aiodrf/?version=3")
        assert [q["sql"] for q in aiodrf] == [q["sql"] for q in drf]
        # One page query with the author joined; no count.
        assert len(aiodrf) == 1
        assert "JOIN" in aiodrf[0]["sql"]
