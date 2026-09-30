"""
Generic views written for DRF keep working when their hooks query the
database: what a project wrote for DRF runs in the action's thread hop.
"""

import asyncio
from unittest import mock

import pytest
from asgiref.sync import sync_to_async
from django.db import connections
from django.db.models import Prefetch
from django.test import TestCase, override_settings
from django.urls import path
from rest_framework import serializers as drf_serializers
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny
from rest_framework.permissions import BasePermission as DRFBasePermission
from rest_framework.test import APIClient, APIRequestFactory

from aiodrf import generics, permissions, serializers, viewsets
from aiodrf.compat import DJANGO_VERSION
from aiodrf.contrib.builtin import prefetch
from aiodrf.contrib.django_filters import DjangoFilterBackend
from aiodrf.pagination import BasePagination
from aiodrf.request import Request
from aiodrf.response import Response
from aiodrf.test import AsyncAPIClient, count_hops
from tests.base import both_transports
from tests.testapp.models import Author, Book, Tag
from tests.testapp.serializers import (
    BookSerializer,
    NestedBookSerializer,
    TagSerializer,
)


class QueryingFieldsSerializer(drf_serializers.ModelSerializer):
    class Meta:
        model = Book
        fields = ["id", "title", "isbn", "author"]

    def get_fields(self):
        fields = super().get_fields()
        if Author.objects.filter(name="Nobody").exists():
            fields.pop("isbn")
        return fields


class PublicViewSet(viewsets.ModelViewSet):
    queryset = Book.objects.all()
    serializer_class = BookSerializer
    authentication_classes = []
    permission_classes = [AllowAny]


class QueryingQuerysetViewSet(PublicViewSet):
    def get_queryset(self):
        # Resolving a parent object first is a common DRF pattern.
        author = Author.objects.filter(name="Ursula").first()
        return Book.objects.filter(author=author)


class QueryingContextViewSet(PublicViewSet):
    def get_serializer_context(self):
        return {**super().get_serializer_context(), "authors": Author.objects.count()}


class QueryingPermissionsViewSet(PublicViewSet):
    def get_permissions(self):
        return super().get_permissions() if Author.objects.exists() else []


class QueryingFieldsViewSet(PublicViewSet):
    serializer_class = QueryingFieldsSerializer


class ScopedViewSet(PublicViewSet):
    # The class-level queryset is not scoped; only ``aget_queryset`` is.
    async def aget_queryset(self):
        author = await Author.objects.aget(name="Ursula")
        return (await super().aget_queryset()).filter(author=author)


class FilteredViewSet(PublicViewSet):
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["title", "pages"]


class NestedViewSet(PublicViewSet):
    serializer_class = NestedBookSerializer


class UnoptimizedNestedBookSerializer(NestedBookSerializer):
    class Meta(NestedBookSerializer.Meta):
        auto_prefetch = False


class UnoptimizedNestedViewSet(PublicViewSet):
    serializer_class = UnoptimizedNestedBookSerializer


VIEWSETS = {
    "queryset": QueryingQuerysetViewSet,
    "context": QueryingContextViewSet,
    "permissions": QueryingPermissionsViewSet,
    "fields": QueryingFieldsViewSet,
    "scoped": ScopedViewSet,
    "filtered": FilteredViewSet,
    "nested": NestedViewSet,
    "unoptimized": UnoptimizedNestedViewSet,
}
urlpatterns = [
    route
    for name, viewset in VIEWSETS.items()
    for route in (
        path(f"{name}/", viewset.as_view({"get": "list", "post": "create"})),
        path(f"{name}/<int:pk>/", viewset.as_view({"get": "retrieve"})),
    )
]


urls = override_settings(ROOT_URLCONF=__name__)


class Fixtures:
    @classmethod
    def setUpTestData(cls):
        cls.ursula = Author.objects.create(name="Ursula")
        cls.other = Author.objects.create(name="Other")
        cls.book = Book.objects.create(title="A", isbn="1", author=cls.ursula)
        Book.objects.create(title="B", isbn="2", author=cls.other)


@both_transports
class _QueryingHookTests(Fixtures):
    async def assert_crud(self, name):
        response = await self.api("get", f"/{name}/")
        assert response.status_code == 200, response.data
        response = await self.api("get", f"/{name}/{self.book.pk}/")
        assert response.status_code == 200, response.data
        data = {"title": "C", "isbn": "3", "author": self.ursula.pk}
        response = await self.api("post", f"/{name}/", data=data)
        assert response.status_code == 201, response.data

    @urls
    async def test_get_queryset(self):
        await self.assert_crud("queryset")
        response = await self.api("get", "/queryset/")
        assert {book["title"] for book in response.data} == {"A", "C"}

    @urls
    async def test_get_serializer_context(self):
        await self.assert_crud("context")

    @urls
    async def test_get_permissions(self):
        await self.assert_crud("permissions")

    @urls
    async def test_serializer_get_fields(self):
        await self.assert_crud("fields")

    @urls
    async def test_async_get_queryset(self):
        response = await self.api("get", "/scoped/")
        assert [book["title"] for book in response.data] == ["A"]

    @urls
    async def test_sync_callers_get_the_async_queryset(self):
        # DjangoModelPermissions, DRF's ``get_object``, the browsable API and
        # drf-spectacular call ``get_queryset()`` synchronously.
        queryset = await sync_to_async(ScopedViewSet().get_queryset)()
        titles = await sync_to_async(list)(queryset.values_list("title", flat=True))
        assert titles == ["A"]


@override_settings(ROOT_URLCONF=__name__)
class HookCostTests(Fixtures, TestCase):
    async def test_hooks_without_queries_cost_no_hop(self):
        with count_hops() as hops:
            response = await AsyncAPIClient().get(f"/scoped/{self.book.pk}/")
        assert response.status_code == 200
        # The whole retrieve is one hop: the awaited queryset stays lazy, and
        # the lookup, the unloaded ``tags`` and the representation share it.
        assert hops.calls == ["RetrieveModelMixin._retrieve"]


class HeaderPolicy(DRFBasePermission):
    # Reads the request; not declared pure, so it is asked in a thread.
    def has_permission(self, request, view):
        return request.headers.get("X-Deny") != "1"


class AiodrfHeaderPolicy(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.headers.get("X-Deny") != "1"


class DRFPolicyViewSet(PublicViewSet):
    permission_classes = [HeaderPolicy]


class AiodrfPolicyViewSet(PublicViewSet):
    permission_classes = [AiodrfHeaderPolicy]


urlpatterns += [
    path("drf-policy/<int:pk>/", DRFPolicyViewSet.as_view({"get": "retrieve"})),
    path("aiodrf-policy/<int:pk>/", AiodrfPolicyViewSet.as_view({"get": "retrieve"})),
]


@override_settings(ROOT_URLCONF=__name__)
class PermissionBaseCostTests(Fixtures, TestCase):
    async def test_a_sync_permission_costs_the_same_on_either_base(self):
        # aiodrf's BasePermission grants object access by default, as DRF's
        # does: that default must not take the object out of the hop.
        calls = []
        for name in ("drf-policy", "aiodrf-policy"):
            with count_hops() as hops:
                response = await AsyncAPIClient().get(f"/{name}/{self.book.pk}/")
            assert response.status_code == 200
            calls.append(hops.calls)
        assert calls[0] == calls[1]


@override_settings(ROOT_URLCONF=__name__)
class PerRequestCostTests(Fixtures, TestCase):
    async def test_auto_prefetch_lookups_are_derived_once(self):
        from unittest import mock

        from aiodrf.contrib.builtin import prefetch

        prefetch.forget_lookups()
        with mock.patch.object(
            prefetch, "related_lookups", wraps=prefetch.related_lookups
        ) as related_lookups:
            for _ in range(3):
                with count_hops() as hops:
                    response = await AsyncAPIClient().get("/nested/")
                assert response.status_code == 200
                # Still one hop: the prefetched list, represented inline.
                assert hops.count == 1, hops.calls
        assert related_lookups.call_count == 1

    def test_auto_prefetch_queries_do_not_grow_with_the_page(self):
        # Through WSGI, where the queries run on this thread's connection and
        # ``assertNumQueries`` sees them.
        client = APIClient()
        books = Book.objects.count()
        with self.assertNumQueries(2):  # books with their author; the tags
            optimized = client.get("/nested/")
        with self.assertNumQueries(1 + 2 * books):  # an author and tags per book
            unoptimized = client.get("/unoptimized/")
        assert optimized.data == unoptimized.data
        assert len(optimized.data) == books


class AuthorBooksSerializer(serializers.ModelSerializer):
    books = NestedBookSerializer(many=True)

    class Meta:
        model = Author
        fields = ["id", "books"]
        auto_prefetch = True


class ManyToManyPrefetchTests(TestCase):
    """
    django-mongodb-backend refuses ``prefetch_related`` of many-to-many
    relations; ``Meta.auto_prefetch`` leaves them to one query per object.
    """

    def lookups(self, queryset, serializer_class):
        queryset = prefetch.auto_prefetch(queryset, serializer_class, serializer_class)
        return queryset.query.select_related, queryset._prefetch_related_lookups

    def test_many_to_many_is_not_prefetched_where_the_backend_cannot(self):
        with mock.patch.object(connections["default"], "vendor", "mongodb"):
            select, prefetched = self.lookups(Book.objects.all(), NestedBookSerializer)
            assert select == {"author": {}}
            assert prefetched == ()
            # A reverse foreign key is still prefetched, the many-to-many
            # relation below it is not.
            select, prefetched = self.lookups(
                Author.objects.all(), AuthorBooksSerializer
            )
            assert prefetched == ("books", "books__author")

    def test_other_backends_prefetch_it(self):
        select, prefetched = self.lookups(Book.objects.all(), NestedBookSerializer)
        assert select == {"author": {}}
        assert prefetched == ("tags",)

    def test_a_prefetch_object_is_the_projects_choice_of_rows_and_is_kept(self):
        class Scoped(NestedBookSerializer):
            class Meta(NestedBookSerializer.Meta):
                prefetch = [Prefetch("tags", queryset=Tag.objects.filter(name="x"))]

        with mock.patch.object(connections["default"], "vendor", "mongodb"):
            _, prefetched = self.lookups(Book.objects.all(), Scoped)
        assert [lookup.prefetch_to for lookup in prefetched] == ["tags"]


class ExplicitPrefetchTests(TestCase):
    """
    A ``Prefetch`` of ``Meta.prefetch`` decides the rows even where the
    view's queryset or the derived lookups reach the same relation.
    """

    @classmethod
    def setUpTestData(cls):
        cls.kept, cls.other = (
            Author.objects.create(name="kept"),
            Author.objects.create(name="other"),
        )
        cls.book = Book.objects.create(title="t", isbn="1", author=cls.kept)
        cls.book.tags.add(Tag.objects.create(name="a"), Tag.objects.create(name="b"))

    def serializer(self, *lookups):
        class Scoped(NestedBookSerializer):
            class Meta(NestedBookSerializer.Meta):
                prefetch = list(lookups)

        return Scoped

    def test_a_string_lookup_of_the_queryset_does_not_replace_it(self):
        scoped = self.serializer(
            Prefetch("tags", queryset=Tag.objects.filter(name="a"))
        )
        for existing in ("tags", "tags__books"):
            queryset = Book.objects.prefetch_related(existing)
            queryset = prefetch.auto_prefetch(queryset, scoped, scoped)
            (book,) = queryset
            assert [tag.name for tag in book.tags.all()] == ["a"], existing

    def test_a_derived_join_does_not_replace_it(self):
        scoped = self.serializer(
            Prefetch("author", queryset=Author.objects.filter(name="kept"))
        )
        queryset = prefetch.auto_prefetch(Book.objects.all(), scoped, scoped)
        assert "author" not in (queryset.query.select_related or {})
        with self.assertNumQueries(3):  # books, authors, tags
            (book,) = queryset
            assert book.author == self.kept


class ScopedTagsSerializer(drf_serializers.ModelSerializer):
    """``Meta.prefetch`` with a queryset chosen per request."""

    tags = drf_serializers.SlugRelatedField(
        slug_field="name", many=True, read_only=True
    )

    class Meta:
        model = Book
        fields = ["id", "title", "tags"]
        auto_prefetch = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        prefix = self.context["request"].query_params.get("tags", "")
        self.Meta = type(
            "Meta",
            (),
            {
                "model": Book,
                "fields": self.Meta.fields,
                "auto_prefetch": True,
                "prefetch": [
                    Prefetch(
                        "tags", queryset=Tag.objects.filter(name__startswith=prefix)
                    )
                ],
            },
        )


class ScopedTagsViewSet(PublicViewSet):
    serializer_class = ScopedTagsSerializer


urlpatterns.append(path("scoped-tags/", ScopedTagsViewSet.as_view({"get": "list"})))


@override_settings(ROOT_URLCONF=__name__)
class RequestScopedPrefetchTests(Fixtures, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.book.tags.add(
            Tag.objects.create(name="a-1"), Tag.objects.create(name="b-1")
        )

    async def test_each_request_gets_its_own_prefetch(self):
        client = AsyncAPIClient()
        prefetch.forget_lookups()
        for scope in ("a", "b", "a", "b"):
            response = await client.get(f"/scoped-tags/?tags={scope}")
            assert response.status_code == 200, response.data
            assert response.data[0]["tags"] == [f"{scope}-1"], scope

    async def test_concurrent_requests_do_not_share_it(self):
        client = AsyncAPIClient()
        prefetch.forget_lookups()
        responses = await asyncio.gather(
            *(client.get(f"/scoped-tags/?tags={scope}") for scope in "abababab")
        )
        assert [r.data[0]["tags"] for r in responses] == [
            [f"{s}-1"] for s in "abababab"
        ]

    async def test_dynamic_field_lookups_are_derived_per_request(self):
        from unittest import mock

        prefetch.forget_lookups()
        with mock.patch.object(
            prefetch, "related_lookups", wraps=prefetch.related_lookups
        ) as derive:
            for scope in ("a", "b"):
                assert (
                    await AsyncAPIClient().get(f"/scoped-tags/?tags={scope}")
                ).status_code == 200
        assert derive.call_count == 2


def test_dynamic_prefetch_fields_are_not_shared_between_instances():
    class Dynamic(drf_serializers.ModelSerializer):
        class Meta:
            model = Book
            fields = ["id", "author"]

        def get_fields(self):
            fields = super().get_fields()
            if self.context.get("expand"):
                fields["author"] = drf_serializers.StringRelatedField()
            return fields

    prefetch.forget_lookups()
    for expanded in (False, True, False, True):
        select, _ = prefetch._lookups_for(
            Dynamic,
            Book,
            lambda expanded=expanded: Dynamic(context={"expand": expanded}),
        )
        assert select == (["author"] if expanded else [])


def test_legacy_prefetch_imports_remain_available():
    from aiodrf import prefetch as legacy
    from aiodrf.contrib.builtin.list_prefetch import PrefetchListSerializer
    from aiodrf.contrib.prefetch import PrefetchListSerializer as LegacyList

    assert legacy.auto_prefetch is prefetch.auto_prefetch
    assert LegacyList is PrefetchListSerializer


def test_prefetch_declines_cache_for_nested_dynamic_fields():
    class DynamicAuthor(drf_serializers.ModelSerializer):
        class Meta:
            model = Author
            fields = ["name"]

        def get_fields(self):
            fields = super().get_fields()
            if self.context.get("titles"):
                fields["books"] = drf_serializers.StringRelatedField(many=True)
            return fields

    class Nested(drf_serializers.ModelSerializer):
        author = DynamicAuthor()

        class Meta:
            model = Book
            fields = ["author"]

    prefetch.forget_lookups()
    for titles in (False, True, False):
        _, related = prefetch._lookups_for(
            Nested, Book, lambda titles=titles: Nested(context={"titles": titles})
        )
        assert related == (["author__books"] if titles else [])


def test_prefetch_declines_cache_for_prebuilt_instance_fields():
    class Output(drf_serializers.ModelSerializer):
        author = drf_serializers.StringRelatedField()

        class Meta:
            model = Book
            fields = ["author"]

    prefetch.forget_lookups()
    assert prefetch._lookups_for(Output, Book, Output)[0] == ["author"]
    changed = Output()
    changed.fields.pop("author")
    assert prefetch._lookups_for(Output, Book, lambda: changed) == ([], [])


def test_prefetch_invalidation_rejects_inflight_publication():
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from unittest import mock

    entered, release = threading.Event(), threading.Event()
    original = prefetch.related_lookups

    def derive(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    prefetch.forget_lookups()
    with (
        ThreadPoolExecutor(max_workers=1) as executor,
        mock.patch.object(prefetch, "related_lookups", side_effect=derive),
    ):
        result = executor.submit(
            prefetch._lookups_for, NestedBookSerializer, Book, NestedBookSerializer
        )
        try:
            assert entered.wait(3)
            prefetch.forget_lookups()
        finally:
            release.set()
        assert result.result(timeout=3)[0] == ["author"]
    with mock.patch.object(prefetch, "related_lookups", wraps=original) as called:
        prefetch._lookups_for(NestedBookSerializer, Book, NestedBookSerializer)
    called.assert_called_once()


# -- Django's AsyncPaginator behind ``apaginate_queryset`` (Django 6.0+) ---------------


class AsyncPages(BasePagination):
    """A paginator written against Django's ``AsyncPaginator``."""

    page_size = 2

    async def apaginate_queryset(self, queryset, request, view=None):
        from django.core.paginator import AsyncPaginator, InvalidPage

        paginator = AsyncPaginator(queryset, self.page_size)
        try:
            self.page = await paginator.apage(request.query_params.get("page", 1))
        except InvalidPage as exc:
            raise NotFound(str(exc)) from exc
        self.count = await paginator.acount()
        return await self.page.aget_object_list()

    def get_paginated_response(self, data):
        return Response(
            {"count": self.count, "page": self.page.number, "results": data}
        )


class AsyncPagedTags(generics.ListAPIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    queryset = Tag.objects.order_by("name")
    serializer_class = TagSerializer
    pagination_class = AsyncPages


urlpatterns.append(path("async-pages/", AsyncPagedTags.as_view()))


class AuthorDestroy(generics.DestroyAPIView):
    # No serializer: DRF's destroy never asks for one.
    authentication_classes = []
    permission_classes = [AllowAny]
    queryset = Author.objects.all()


urlpatterns.append(path("authors/<int:pk>/", AuthorDestroy.as_view()))


@both_transports
class _NoSerializerTests:
    @urls
    async def test_destroy_needs_no_serializer_class(self):
        # Found by DRF's own suite run against aiodrf (``nox -s drf_parity``).
        author = await Author.objects.acreate(name="Ada")
        response = await self.api("delete", f"/authors/{author.pk}/")
        assert response.status_code == 204
        assert not await Author.objects.filter(pk=author.pk).aexists()


@pytest.mark.skipif(
    DJANGO_VERSION < (6, 0), reason="AsyncPaginator is new in Django 6.0"
)
@both_transports
class _AsyncPaginatorTests:
    @urls
    async def test_pages_come_from_djangos_async_paginator(self):
        await Tag.objects.abulk_create([Tag(name=name) for name in "abcde"])
        response = await self.api("get", "/async-pages/?page=2")
        assert response.status_code == 200
        body = response.json()
        assert (body["count"], body["page"]) == (5, 2)
        assert [tag["name"] for tag in body["results"]] == ["c", "d"]

    @urls
    async def test_a_page_that_does_not_exist_is_a_404(self):
        response = await self.api("get", "/async-pages/?page=9")
        assert response.status_code == 404

    async def test_synchronous_callers_reach_it_through_the_bridge(self):
        # The browsable API and code written for DRF call ``paginate_queryset``.
        await Tag.objects.acreate(name="a")
        request = Request(APIRequestFactory().get("/", {"page": 1}))
        page = await sync_to_async(AsyncPages().paginate_queryset)(
            Tag.objects.order_by("name"), request
        )
        assert [tag.name for tag in page] == ["a"]


@pytest.mark.django_db(transaction=True)
async def test_views_parameterized_by_their_model_for_type_checkers():
    class AuthorSerializer(drf_serializers.ModelSerializer):
        class Meta:
            model = Author
            fields = ["name"]

    class Detail(generics.RetrieveAPIView[Author]):
        authentication_classes = []
        permission_classes = [AllowAny]
        queryset = Author.objects.all()
        serializer_class = AuthorSerializer

    class Authors(viewsets.ModelViewSet[Author]):
        queryset = Author.objects.all()

    author = await Author.objects.acreate(name="ada")
    request = APIRequestFactory().get("/")
    response = await Detail.as_view()(request, pk=author.pk)
    assert response.data == {"name": "ada"}
    assert Authors.__mro__[1:] == viewsets.ModelViewSet.__mro__
