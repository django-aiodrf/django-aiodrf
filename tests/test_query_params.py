"""
Query parameters validated by a serializer (``query_serializer_class``).

The query string is validated like a request body: the serializer's
``validated_data``, or DRF's 400 response with its errors.
"""

import threading

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase, override_settings
from django.urls import path
from django.utils.asyncio import async_unsafe
from rest_framework import serializers
from rest_framework.permissions import AllowAny

from aiodrf import generics, viewsets
from aiodrf.decorators import action
from aiodrf.response import Response
from aiodrf.routers import SimpleRouter
from aiodrf.test import AsyncAPIRequestFactory, count_hops
from aiodrf.views import APIView
from tests.base import both_transports
from tests.testapp.models import Author


class Filters(serializers.Serializer):
    q = serializers.CharField(required=False)
    tag = serializers.ListField(child=serializers.CharField(), required=False)
    limit = serializers.IntegerField(min_value=1, max_value=100, default=10)


class Search(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    query_serializer_class = Filters

    async def get(self, request):
        return Response(await self.aget_validated_query_params())


class SyncSearch(Search):
    def get(self, request):
        return Response(self.get_validated_query_params())


class AuthorFilters(serializers.Serializer):
    name = serializers.CharField(required=False)


class AuthorNames(serializers.ModelSerializer):
    class Meta:
        model = Author
        fields = ["name"]


class AuthorList(generics.ListAPIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    serializer_class = AuthorNames
    query_serializer_class = AuthorFilters

    def get_queryset(self):
        # Runs in the worker thread with the rest of ``list``.
        queryset = Author.objects.order_by("name")
        name = self.get_validated_query_params().get("name")
        return queryset.filter(name=name) if name else queryset


class ByAction(viewsets.ViewSet):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get_query_serializer_class(self):
        return AuthorFilters if self.action == "authors" else Filters

    async def list(self, request):
        return Response(await self.aget_validated_query_params())

    @action(detail=False)
    async def authors(self, request):
        return Response(await self.aget_validated_query_params())


class SomeActions(viewsets.ViewSet):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get_query_serializer_class(self):
        return Filters if self.action == "list" else None

    async def list(self, request):
        return Response(await self.aget_validated_query_params())

    @action(detail=False)
    async def plain(self, request):
        return Response({})


router = SimpleRouter()
router.register("by-action", ByAction, basename="by-action")
router.register("some-actions", SomeActions, basename="some-actions")

urlpatterns = [
    path("search/", Search.as_view()),
    path("search/sync/", SyncSearch.as_view()),
    path("authors/", AuthorList.as_view()),
    *router.urls,
]


class _QueryParamsTests:
    @classmethod
    def setUpClass(cls):
        cls.enterClassContext(override_settings(ROOT_URLCONF=__name__))
        super().setUpClass()

    async def test_valid_parameters_become_validated_data(self):
        for url in ("/search/", "/search/sync/"):
            response = await self.api("get", url + "?q=x&tag=a&tag=b&limit=5")
            assert response.status_code == 200, response.content
            assert response.json() == {"q": "x", "tag": ["a", "b"], "limit": 5}

    async def test_defaults_apply_and_absent_optional_fields_are_left_out(self):
        response = await self.api("get", "/search/")
        assert response.json() == {"limit": 10}

    async def test_invalid_parameters_answer_400_with_drfs_errors(self):
        expected = Filters(data={"limit": "0"})
        expected.is_valid()
        for url in ("/search/", "/search/sync/"):
            response = await self.api("get", url + "?limit=0")
            assert response.status_code == 400
            assert response.json() == expected.errors

    async def test_a_generic_views_queryset_reads_them_in_the_worker(self):
        await Author.objects.abulk_create([Author(name="Ada"), Author(name="Bo")])
        response = await self.api("get", "/authors/?name=Bo")
        assert response.json() == [{"name": "Bo"}]
        response = await self.api("get", "/authors/")
        assert response.json() == [{"name": "Ada"}, {"name": "Bo"}]

    async def test_the_serializer_class_can_depend_on_the_action(self):
        response = await self.api("get", "/by-action/authors/?name=Bo&limit=0")
        assert response.json() == {"name": "Bo"}
        response = await self.api("get", "/by-action/?limit=0")
        assert response.status_code == 400


both_transports(_QueryParamsTests)


class HopTests(TestCase):
    async def call(self, view, query=""):
        with count_hops() as hops:
            response = await view(AsyncAPIRequestFactory().get("/" + query))
        return response, hops

    async def test_a_declarative_serializer_validates_on_the_event_loop(self):
        response, hops = await self.call(Search.as_view(), "?tag=a")
        assert response.data == {"tag": ["a"], "limit": 10}
        assert hops.count == 0, hops.calls

    async def test_a_factory_of_the_projects_runs_in_one_hop_with_the_validation(self):
        built = []

        class Custom(Search):
            @async_unsafe("query serializer factory on loop")
            def get_query_serializer_class(self):
                built.append(threading.get_ident())
                return Filters

        response, hops = await self.call(Custom.as_view(), "?limit=3")
        assert response.data == {"limit": 3}
        assert hops.count == 1, hops.calls
        assert built[0] != threading.get_ident()

    async def test_a_serializer_with_code_of_its_own_is_built_in_the_worker(self):
        class Querying(Filters):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.authors = Author.objects.count()

        view = Search.as_view(query_serializer_class=Querying)
        response, hops = await self.call(view)
        assert response.status_code == 200
        assert hops.count == 1, hops.calls

    async def test_async_field_validation_is_awaited(self):
        class Checked(Filters):
            async def validate_q(self, value):
                if not await Author.objects.filter(name=value).aexists():
                    raise serializers.ValidationError("unknown author")
                return value

        await Author.objects.acreate(name="Ada")
        response, _ = await self.call(
            Search.as_view(query_serializer_class=Checked), "?q=Ada"
        )
        assert response.data == {"q": "Ada", "limit": 10}
        response, _ = await self.call(
            Search.as_view(query_serializer_class=Checked), "?q=Bo"
        )
        assert response.status_code == 400
        assert response.data == {"q": ["unknown author"]}

    async def test_the_parameters_are_validated_once_per_request(self):
        validated = []

        class Counted(Filters):
            def validate(self, attrs):
                validated.append(attrs)
                return attrs

        class Twice(Search):
            query_serializer_class = Counted

            async def get(self, request):
                first = await self.aget_validated_query_params()
                assert first is await self.aget_validated_query_params()
                return Response(first)

        response, _ = await self.call(Twice.as_view())
        assert response.status_code == 200
        assert len(validated) == 1

    async def test_a_view_without_a_query_serializer_says_so(self):
        class Missing(Search):
            query_serializer_class = None

        with pytest.raises(AssertionError, match="query_serializer_class"):
            await self.call(Missing.as_view())

    def test_the_factories_must_stay_synchronous(self):
        class AsyncFactory(Search):
            async def get_query_serializer_class(self):
                return Filters

        with pytest.raises(ImproperlyConfigured, match="get_query_serializer_class"):
            AsyncFactory.as_view()


class SchemaTests(TestCase):
    def schema(self, schema_class):
        from drf_spectacular.generators import SchemaGenerator
        from drf_spectacular.validation import validate_schema

        rest_framework = {"DEFAULT_SCHEMA_CLASS": schema_class}
        with override_settings(ROOT_URLCONF=__name__, REST_FRAMEWORK=rest_framework):
            schema = SchemaGenerator().get_schema(request=None, public=True)
        validate_schema(schema)
        return schema

    def parameters(self, schema, path, method="get"):
        return {
            parameter["name"]: parameter
            for parameter in schema["paths"][path][method].get("parameters", [])
        }

    def test_the_query_serializer_is_documented_as_query_parameters(self):
        schema = self.schema("aiodrf.contrib.spectacular.AutoSchema")
        parameters = self.parameters(schema, "/search/")
        assert set(parameters) == {"q", "tag", "limit"}
        assert all(parameter["in"] == "query" for parameter in parameters.values())
        assert parameters["tag"]["schema"] == {
            "type": "array",
            "items": {"type": "string"},
        }
        assert parameters["limit"]["schema"]["maximum"] == 100
        # Per action, from ``get_query_serializer_class``.
        assert set(self.parameters(schema, "/by-action/authors/")) == {"name"}
        assert set(self.parameters(schema, "/by-action/")) == {"q", "tag", "limit"}

    def test_an_action_without_a_query_serializer_documents_none(self):
        from unittest import mock

        with mock.patch("drf_spectacular.openapi.warn") as warn:
            schema = self.schema("aiodrf.contrib.spectacular.AutoSchema")
        assert warn.call_args_list == []
        assert set(self.parameters(schema, "/some-actions/")) == {"q", "tag", "limit"}
        assert self.parameters(schema, "/some-actions/plain/") == {}

    def test_spectacular_alone_does_not_know_about_it(self):
        schema = self.schema("drf_spectacular.openapi.AutoSchema")
        assert self.parameters(schema, "/search/") == {}
