"""
djangorestframework-datatables: a renderer, a filter backend and a paginator
that work together. The backend counts before and after filtering and leaves
the counts on the view; the paginator picks them up; the renderer reads the
request's ``columns[...]`` parameters and asks the view for its serializer.
"""

from urllib.parse import urlencode

from django.test import TestCase, override_settings
from django.urls import path
from rest_framework import generics as drf_generics
from rest_framework.permissions import AllowAny
from rest_framework.renderers import JSONRenderer
from rest_framework.serializers import ModelSerializer
from rest_framework_datatables.filters import DatatablesFilterBackend
from rest_framework_datatables.pagination import DatatablesPageNumberPagination
from rest_framework_datatables.renderers import DatatablesRenderer

from aiodrf import generics
from aiodrf.test import AsyncAPIClient, count_hops
from tests.base import both_transports
from tests.testapp.models import Author, Book


class BookSerializer(ModelSerializer):
    class Meta:
        model = Book
        fields = ["id", "title", "pages"]
        datatables_always_serialize = ("id",)


class Policies:
    queryset = Book.objects.all()
    serializer_class = BookSerializer
    authentication_classes = []
    permission_classes = [AllowAny]
    renderer_classes = [JSONRenderer, DatatablesRenderer]
    filter_backends = [DatatablesFilterBackend]
    pagination_class = DatatablesPageNumberPagination


class DRFBooks(Policies, drf_generics.ListAPIView):
    pass


class Books(Policies, generics.ListAPIView):
    pass


urlpatterns = [path("drf/", DRFBooks.as_view()), path("aiodrf/", Books.as_view())]


def query(search="", length=2, start=0, draw=3):
    """What DataTables sends: the columns, the order, the search and the page."""
    return "?" + urlencode(
        {
            "format": "datatables",
            "draw": draw,
            "columns[0][data]": "title",
            "columns[0][searchable]": "true",
            "columns[0][orderable]": "true",
            "columns[1][data]": "pages",
            "columns[1][searchable]": "false",
            "columns[1][orderable]": "true",
            "order[0][column]": "1",
            "order[0][dir]": "desc",
            "search[value]": search,
            "search[regex]": "false",
            "start": start,
            "length": length,
        }
    )


class Fixtures:
    @classmethod
    def setUpTestData(cls):
        author = Author.objects.create(name="Ursula")
        for number, (title, pages) in enumerate(
            [
                ("Earthsea", 180),
                ("Tehanu", 250),
                ("The Dispossessed", 380),
                ("Lavinia", 290),
            ]
        ):
            Book.objects.create(
                title=title, isbn=f"97800000000{number}", pages=pages, author=author
            )


@both_transports
class _DatatablesTests(Fixtures):
    async def same(self, url):
        drf = await self.api("get", f"/drf/{url}")
        aiodrf = await self.api("get", f"/aiodrf/{url}")
        assert aiodrf.status_code == drf.status_code == 200
        assert aiodrf.content == drf.content
        return aiodrf.json()

    @override_settings(ROOT_URLCONF=__name__)
    async def test_page_order_and_counts(self):
        body = await self.same(query())
        assert (body["draw"], body["recordsTotal"], body["recordsFiltered"]) == (
            3,
            4,
            4,
        )
        assert [row["title"] for row in body["data"]] == ["The Dispossessed", "Lavinia"]
        # Only the requested columns, and what the serializer always sends.
        assert set(body["data"][0]) == {"id", "title", "pages"}
        second = await self.same(query(start=2))
        assert [row["title"] for row in second["data"]] == ["Tehanu", "Earthsea"]

    @override_settings(ROOT_URLCONF=__name__)
    async def test_search_counts_before_and_after_filtering(self):
        body = await self.same(query(search="the"))
        assert (body["recordsTotal"], body["recordsFiltered"]) == (4, 1)
        assert [row["title"] for row in body["data"]] == ["The Dispossessed"]

    @override_settings(ROOT_URLCONF=__name__)
    async def test_other_formats_are_neither_filtered_nor_paginated(self):
        body = await self.same("?search[value]=the")
        assert len(body) == 4


@override_settings(ROOT_URLCONF=__name__)
class DatatablesCostTests(Fixtures, TestCase):
    async def test_filtering_and_paging_share_the_view_in_one_hop(self):
        with count_hops() as hops:
            response = await AsyncAPIClient().get(f"/aiodrf/{query(search='the')}")
        assert response.status_code == 200
        # The backend's counts, left on the view, reach the paginator in the
        # same hop. The renderer is a subclass aiodrf does not know, so Django
        # renders the response in a thread (not an aiodrf hop).
        assert hops.calls == ["ListModelMixin._list"]
