"""rest-filters query validation, queryset scoping and schema parity with DRF."""

import asyncio

from django.test import SimpleTestCase, override_settings
from django.urls import path
from drf_spectacular.generators import SchemaGenerator
from rest_filters import Filter, FilterSet
from rest_filters.backends import FilterBackend
from rest_framework import generics as drf_generics
from rest_framework import serializers

from aiodrf.generics import ListAPIView
from tests.base import both_transports
from tests.ecosystem.base import same_response
from tests.testapp.models import Author, Book


class BookFilters(FilterSet):
    title = Filter(serializers.CharField(min_length=2), lookup="icontains")
    min_pages = Filter(
        serializers.IntegerField(min_value=1), field="pages", lookup="gte"
    )
    author = Filter(serializers.PrimaryKeyRelatedField(queryset=Author.objects.all()))

    def get_serializer_context(self, param):
        # A real sync ORM read makes accidental event-loop execution fail.
        assert Author.objects.exists()
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise AssertionError("Synchronous filter hooks ran on the event loop")
        return super().get_serializer_context(param)


class BookSerializer(serializers.ModelSerializer):
    class Meta:
        model = Book
        fields = ["id", "title", "pages", "author"]


class Policies:
    # The filter backend must narrow this authorized scope, never replace it.
    queryset = Book.objects.filter(author__name="Ursula").order_by("pk")
    serializer_class = BookSerializer
    filter_backends = [FilterBackend]
    filterset_class = BookFilters
    authentication_classes = []
    permission_classes = []
    pagination_class = None


class DRFBooks(Policies, drf_generics.ListAPIView):
    pass


class Books(Policies, ListAPIView):
    pass


urlpatterns = [path("drf/", DRFBooks.as_view()), path("aiodrf/", Books.as_view())]
configured = override_settings(ROOT_URLCONF=__name__)


@both_transports
class _RestFiltersTests:
    @classmethod
    def setUpTestData(cls):
        ursula = Author.objects.create(name="Ursula")
        cls.other = Author.objects.create(name="Octavia")
        for index, (title, pages, author) in enumerate(
            [
                ("Earthsea", 200, ursula),
                ("Lavinia", 300, ursula),
                ("Kindred", 400, cls.other),
            ]
        ):
            Book.objects.create(
                title=title, pages=pages, author=author, isbn=str(index)
            )

    @configured
    async def test_values_errors_and_unknown_parameters_match_drf(self):
        for query, status, titles in [
            ({}, 200, ["Earthsea", "Lavinia"]),
            ({"title": "EAR"}, 200, ["Earthsea"]),
            ({"min_pages": "250"}, 200, ["Lavinia"]),
            ({"title": ""}, 200, ["Earthsea", "Lavinia"]),
            ({"min_pages": "invalid"}, 400, None),
            ({"min_pages": "0"}, 400, None),
            ({"title": "a"}, 400, None),
            ({"author": "99999"}, 400, None),
            ({"unknown": "value"}, 400, None),
            ({"author": str(self.other.pk)}, 200, []),
        ]:
            with self.subTest(query=query):
                reference = await self.api("get", "/drf/", data=query)
                actual = await self.api("get", "/aiodrf/", data=query)
                assert same_response(reference, actual), (reference.data, actual.data)
                assert actual.status_code == status, actual.data
                if titles is not None:
                    assert [item["title"] for item in actual.data] == titles

    @configured
    async def test_validation_state_does_not_escape_the_request(self):
        invalid = await self.api("get", "/aiodrf/", data={"min_pages": "bad"})
        assert invalid.status_code == 400
        valid = await self.api("get", "/aiodrf/", data={"min_pages": "250"})
        assert [item["title"] for item in valid.data] == ["Lavinia"]
        unfiltered = await self.api("get", "/aiodrf/")
        assert len(unfiltered.data) == 2


@configured
class SchemaTests(SimpleTestCase):
    def test_spectacular_keeps_filter_names_types_and_constraints(self):
        schema = SchemaGenerator().get_schema(public=True)
        reference = schema["paths"]["/drf/"]["get"]["parameters"]
        actual = schema["paths"]["/aiodrf/"]["get"]["parameters"]
        assert actual == reference
        fields = {field["name"]: field for field in actual}
        assert set(fields) == {"title", "min_pages", "author"}
        assert fields["min_pages"]["schema"]["minimum"] == 1
        assert fields["title"]["schema"]["type"] == "string"
