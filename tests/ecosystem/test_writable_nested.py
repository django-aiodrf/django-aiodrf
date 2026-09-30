"""
drf-writable-nested: nested create and update of FK, reverse-FK and M2M
children in aiodrf's create and update actions.

The package saves the children one by one inside ``serializer.save()``,
which aiodrf runs in its worker thread. The package overrides ``save()``, and
aiodrf leaves an overridden ``save()`` to own its transaction, so
``AIODRF["ATOMIC_SAVE"]`` does not apply: a child refused during the save
leaves the parent behind, as in DRF without ``ATOMIC_REQUESTS``. A
``perform_create`` with ``transaction.atomic()`` makes it one unit.
"""

import pytest
from django.db import transaction
from django.test import override_settings
from django.urls import include, path
from drf_writable_nested.mixins import UniqueFieldsMixin
from drf_writable_nested.serializers import WritableNestedModelSerializer
from rest_framework import serializers
from rest_framework import viewsets as drf_viewsets
from rest_framework.permissions import AllowAny
from rest_framework.routers import SimpleRouter

from aiodrf import viewsets
from aiodrf.test import AsyncAPIClient, count_hops
from tests.base import both_transports
from tests.testapp.models import Author, Book, Tag


# ``UniqueFieldsMixin`` checks unique fields at save time, where the child's
# instance is known (the package's answer to DRF's nested-unique limitation).
class NestedTagSerializer(UniqueFieldsMixin, serializers.ModelSerializer):
    class Meta:
        model = Tag
        fields = ["id", "name"]


class NestedAuthorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Author
        fields = ["id", "name"]


class NestedBookSerializer(WritableNestedModelSerializer):
    author = NestedAuthorSerializer()  # FK, saved before the book
    tags = NestedTagSerializer(many=True, required=False)  # M2M, after

    class Meta:
        model = Book
        fields = ["id", "title", "isbn", "author", "tags"]


class NestedAuthorBookSerializer(UniqueFieldsMixin, serializers.ModelSerializer):
    class Meta:
        model = Book
        fields = ["id", "title", "isbn"]


class NestedAuthorWithBooksSerializer(WritableNestedModelSerializer):
    books = NestedAuthorBookSerializer(many=True)  # reverse FK, after

    class Meta:
        model = Author
        fields = ["id", "name", "books"]


class Open:
    authentication_classes = []
    permission_classes = [AllowAny]


def routes(base):
    router = SimpleRouter()
    router.register(
        "books",
        type(
            "Books",
            (Open, base),
            {"queryset": Book.objects.all(), "serializer_class": NestedBookSerializer},
        ),
        basename="book",
    )
    router.register(
        "authors",
        type(
            "Authors",
            (Open, base),
            {
                "queryset": Author.objects.order_by("pk"),
                "serializer_class": NestedAuthorWithBooksSerializer,
            },
        ),
        basename="author",
    )
    return router.urls


class AtomicCreate(viewsets.ModelViewSet):
    # The remedy for a save() that is not covered by ATOMIC_SAVE.
    def perform_create(self, serializer):
        with transaction.atomic():
            serializer.save()


urlpatterns = [
    path("drf/", include((routes(drf_viewsets.ModelViewSet), "drf"))),
    path("aiodrf/", include((routes(viewsets.ModelViewSet), "aiodrf"))),
    path("atomic/", include((routes(AtomicCreate), "atomic"))),
]
urls = override_settings(ROOT_URLCONF=__name__)


def without_ids(data):
    if isinstance(data, (list, tuple)):
        return [without_ids(item) for item in data]
    if isinstance(data, dict):
        return {key: without_ids(value) for key, value in data.items() if key != "id"}
    return data


def book(prefix):
    return {
        "title": "Earthsea",
        "isbn": f"{prefix}-1",
        "author": {"name": f"{prefix}-Ursula"},
        "tags": [{"name": f"{prefix}-fantasy"}, {"name": f"{prefix}-classic"}],
    }


def duplicates(prefix):
    return {
        "name": f"{prefix}-Ursula",
        "books": [
            {"title": "A", "isbn": f"{prefix}-1"},
            {"title": "B", "isbn": f"{prefix}-1"},
        ],
    }


@both_transports
class _WritableNestedTests:
    @urls
    async def test_fk_and_m2m_children_are_created_and_updated_as_in_drf(self):
        results = {}
        for name in ("drf", "aiodrf"):
            created = await self.api("post", f"/{name}/books/", data=book(name))
            assert created.status_code == 201, created.data
            # Children with a pk are updated; the one left out is unlinked.
            changed = {
                "author": {
                    "id": created.data["author"]["id"],
                    "name": f"{name}-Le Guin",
                },
                "tags": [created.data["tags"][1]],
            }
            updated = await self.api(
                "patch", f"/{name}/books/{created.data['id']}/", data=changed
            )
            assert updated.status_code == 200, updated.data
            results[name] = (created.data, updated.data)
        drf, aiodrf = (
            without_ids(results[name]).__repr__().replace(f"{name}-", "")
            for name in results
        )
        assert drf == aiodrf
        created, updated = results["aiodrf"]
        # The update changed the existing author instead of creating another.
        assert updated["author"]["id"] == created["author"]["id"]
        assert await Author.objects.filter(name="aiodrf-Le Guin").acount() == 1

    @urls
    async def test_reverse_fk_children_are_created_and_removed_as_in_drf(self):
        results = {}
        for name in ("drf", "aiodrf"):
            payload = {
                "name": "Ursula",
                "books": [
                    {"title": "A", "isbn": f"{name}-a"},
                    {"title": "B", "isbn": f"{name}-b"},
                ],
            }
            created = await self.api("post", f"/{name}/authors/", data=payload)
            assert created.status_code == 201, created.data
            kept = created.data["books"][0]
            updated = await self.api(
                "put",
                f"/{name}/authors/{created.data['id']}/",
                data={"name": "Ursula", "books": [{**kept, "title": "A2"}]},
            )
            assert updated.status_code == 200, updated.data
            results[name] = repr(without_ids(updated.data)).replace(f"{name}-", "")
            # The book left out of the list was deleted by the package.
            assert await Book.objects.filter(author=created.data["id"]).acount() == 1
        assert results["drf"] == results["aiodrf"]
        assert results["aiodrf"] == repr(
            {"name": "Ursula", "books": [{"title": "A2", "isbn": "a"}]}
        )

    @urls
    async def test_a_child_refused_in_the_save_keeps_the_parent_as_in_drf(self):
        # Each child is valid alone; the second one's unique isbn is checked
        # by ``UniqueFieldsMixin`` in the save, after the author and the
        # first book were inserted. The package overrides ``save()``, which
        # ``ATOMIC_SAVE`` leaves alone by design (implementation guide section 7).
        responses = {}
        for name in ("drf", "aiodrf"):
            responses[name] = await self.api(
                "post", f"/{name}/authors/", data=duplicates(name)
            )
            assert await Author.objects.filter(name=f"{name}-Ursula").aexists()
            assert await Book.objects.filter(isbn=f"{name}-1").acount() == 1
        assert responses["drf"].status_code == responses["aiodrf"].status_code == 400
        assert responses["drf"].data == responses["aiodrf"].data

    @urls
    async def test_an_atomic_perform_create_rolls_the_parent_back(self):
        response = await self.api("post", "/atomic/authors/", data=duplicates("atomic"))
        assert response.status_code == 400
        assert not await Author.objects.filter(name="atomic-Ursula").aexists()
        assert not await Book.objects.filter(isbn="atomic-1").aexists()

    @urls
    async def test_invalid_children_are_400_with_drfs_errors(self):
        payload = {"title": "X", "isbn": "x1", "author": {}, "tags": [{"name": ""}]}
        drf = await self.api("post", "/drf/books/", data=payload)
        aiodrf = await self.api("post", "/aiodrf/books/", data=payload)
        assert drf.status_code == aiodrf.status_code == 400
        assert drf.data == aiodrf.data
        assert await Book.objects.acount() == 0


@pytest.mark.django_db(transaction=True)
async def test_the_nested_create_costs_one_hop():
    with count_hops() as hops, override_settings(ROOT_URLCONF=__name__):
        response = await AsyncAPIClient().post(
            "/aiodrf/books/", book("hop"), format="json"
        )
    assert response.status_code == 201, response.data
    assert hops.calls == ["CreateModelMixin._create"]
