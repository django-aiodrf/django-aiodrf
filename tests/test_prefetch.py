"""aiodrf.contrib.builtin.list_prefetch: one awaited step per list, then DRF's representation."""

import asyncio
import threading

import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings
from django.utils.asyncio import async_unsafe
from rest_framework import serializers
from rest_framework.exceptions import APIException
from rest_framework.pagination import PageNumberPagination

from aiodrf import aio, generics
from aiodrf.contrib.builtin.list_prefetch import PrefetchListSerializer
from aiodrf.test import AsyncAPIRequestFactory, count_hops
from tests.testapp.models import Author, Book

pytestmark = pytest.mark.django_db(transaction=True)

prefetches = []


class RatedAuthors(PrefetchListSerializer):
    prefetch_related = ["books"]

    async def aprefetch(self, instances):
        prefetches.append([author.name for author in instances])
        await asyncio.sleep(
            0
        )  # an awaited call for all items, e.g. a bulk HTTP request
        for author in instances:
            author.rating = len(author.name)


class AuthorSerializer(serializers.ModelSerializer):
    rating = serializers.IntegerField(read_only=True)
    titles = serializers.SerializerMethodField()

    class Meta:
        model = Author
        fields = ["name", "rating", "titles"]
        list_serializer_class = RatedAuthors

    def get_titles(self, author):
        # Reads the prefetched relation: no query per author.
        return [book.title for book in author.books.all()]


class Pages(PageNumberPagination):
    page_size = 2


class AuthorList(generics.ListAPIView):
    authentication_classes = []
    permission_classes = []
    serializer_class = AuthorSerializer
    queryset = Author.objects.order_by("name")


async def library():
    ada = await Author.objects.acreate(name="Ada")
    await Author.objects.acreate(name="Bo")
    await Author.objects.acreate(name="Cyd")
    await Book.objects.acreate(title="Notes", isbn="1", author=ada)
    await Book.objects.acreate(title="Letters", isbn="2", author=ada)


@pytest.fixture(autouse=True)
def _clear():
    prefetches.clear()


async def get(view):
    return await view(AsyncAPIRequestFactory().get("/"))


@pytest.mark.aiodrf_settings(SERIALIZER_BACKEND_FALLBACK="drf")
async def test_one_prefetch_per_list_and_items_in_order():
    await library()
    response = await get(AuthorList.as_view())
    assert response.status_code == 200
    assert response.data == [
        {"name": "Ada", "rating": 3, "titles": ["Notes", "Letters"]},
        {"name": "Bo", "rating": 2, "titles": []},
        {"name": "Cyd", "rating": 3, "titles": []},
    ]
    assert prefetches == [["Ada", "Bo", "Cyd"]]


@pytest.mark.aiodrf_settings(SERIALIZER_BACKEND_FALLBACK="drf")
async def test_a_page_is_prefetched_not_the_whole_queryset():
    await library()
    response = await get(AuthorList.as_view(pagination_class=Pages))
    assert [row["name"] for row in response.data["results"]] == ["Ada", "Bo"]
    assert prefetches == [["Ada", "Bo"]]


async def test_relations_are_prefetched_before_aprefetch_runs():
    await library()
    seen = []

    class Checking(RatedAuthors):
        async def aprefetch(self, instances):
            seen.extend(
                "books" in author._prefetched_objects_cache for author in instances
            )
            await super().aprefetch(instances)

    class CheckingSerializer(AuthorSerializer):
        class Meta(AuthorSerializer.Meta):
            list_serializer_class = Checking

    data = await CheckingSerializer(Author.objects.order_by("name"), many=True).adata()
    assert data[0]["titles"] == ["Notes", "Letters"]
    assert seen == [True, True, True]


@pytest.mark.aiodrf_settings(SERIALIZER_BACKEND_FALLBACK="drf")
async def test_hops_the_query_the_prefetch_and_the_representation():
    await library()
    with count_hops() as hops:
        response = await get(AuthorList.as_view(pagination_class=Pages))
    assert response.status_code == 200
    # The page is evaluated in the action's hop; the items are represented
    # in one more. Django's own aprefetch_related_objects is not an aiodrf
    # hop and is not counted.
    assert hops.calls == ["ListModelMixin._list", "PrefetchListSerializer._represent"]


async def test_synchronous_callers_get_the_same_representation():
    await library()

    def represent():
        return AuthorSerializer(Author.objects.order_by("name"), many=True).data

    data = await sync_to_async(represent)()
    assert data[0] == {"name": "Ada", "rating": 3, "titles": ["Notes", "Letters"]}
    assert prefetches == [["Ada", "Bo", "Cyd"]]


@pytest.mark.aiodrf_settings(SERIALIZER_BACKEND_FALLBACK="drf")
async def test_an_error_while_prefetching_is_drfs_error():
    await library()

    class Unavailable(APIException):
        status_code = 503
        default_detail = "Rating service unavailable."

    class Failing(RatedAuthors):
        async def aprefetch(self, instances):
            raise Unavailable

    class FailingSerializer(AuthorSerializer):
        class Meta(AuthorSerializer.Meta):
            list_serializer_class = Failing

    response = await get(AuthorList.as_view(serializer_class=FailingSerializer))
    assert response.status_code == 503
    assert response.data == {"detail": "Rating service unavailable."}


@pytest.mark.aiodrf_settings(SERIALIZER_BACKEND_FALLBACK="drf")
async def test_cancelling_the_request_cancels_the_prefetch():
    await library()
    started, finished = asyncio.Event(), []

    class Slow(RatedAuthors):
        async def aprefetch(self, instances):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                finished.append(threading.get_ident())

    class SlowSerializer(AuthorSerializer):
        class Meta(AuthorSerializer.Meta):
            list_serializer_class = Slow

    task = asyncio.create_task(get(AuthorList.as_view(serializer_class=SlowSerializer)))
    # A request that ends before prefetching fails here rather than hangs.
    await asyncio.wait(
        {task, asyncio.ensure_future(started.wait())},
        return_when=asyncio.FIRST_COMPLETED,
    )
    assert not task.done(), task.result()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished == [threading.get_ident()]


@pytest.mark.aiodrf_settings(
    REPRESENTATION_MODE="thread", SERIALIZER_BACKEND_FALLBACK="drf"
)
async def test_a_loaded_list_costs_one_hop_for_its_representation():
    await library()
    authors = [author async for author in Author.objects.order_by("name")]
    with count_hops() as hops:
        data = await AuthorSerializer(authors, many=True).adata()
    assert [row["rating"] for row in data] == [3, 2, 3]
    # adata() first classifies a serializer class with code of its own in the
    # worker (in a generic view that happens inside the action's hop).
    assert hops.calls == ["try_data", "PrefetchListSerializer._represent"]


async def test_items_with_async_fields_of_their_own_are_still_awaited():
    await library()

    class AsyncTitles(AuthorSerializer):
        async def get_titles(self, author):
            return [book.title async for book in author.books.all()]

    data = await AsyncTitles(Author.objects.order_by("name"), many=True).adata()
    assert data[0] == {"name": "Ada", "rating": 3, "titles": ["Notes", "Letters"]}


class Values(PrefetchListSerializer):
    pass


class ValueSerializer(serializers.Serializer):
    value = serializers.IntegerField()

    class Meta:
        list_serializer_class = Values


class GuardedList(list):
    @async_unsafe("a list subclass was iterated on the event loop")
    def __iter__(self):
        return super().__iter__()


@pytest.mark.aiodrf_settings(SERIALIZER_BACKEND_FALLBACK="drf")
async def test_only_exact_lists_are_read_on_the_loop():
    serializer = ValueSerializer(GuardedList([{"value": 3}]), many=True)
    assert await aio.data(serializer) == [{"value": 3}]


async def test_a_manager_is_evaluated_in_the_worker(worker_connections):
    await Author.objects.acreate(name="Ursula")

    class Guarded(type(Author.objects)):
        @async_unsafe("a manager's all() ran on the event loop")
        def all(self):
            return super().all()

    manager = Guarded()
    manager.model = Author

    class Names(PrefetchListSerializer):
        pass

    class NameSerializer(serializers.Serializer):
        name = serializers.CharField()

        class Meta:
            list_serializer_class = Names

    assert await aio.data(NameSerializer(manager, many=True)) == [{"name": "Ursula"}]


class StaticAuthorSerializer(serializers.ModelSerializer):
    """Compiles: the list's own awaited step must still run."""

    class Meta:
        model = Author
        fields = ["name"]
        list_serializer_class = RatedAuthors


@pytest.mark.parametrize("backend", ["drf", "msgspec", "python"])
async def test_a_compiled_backend_runs_the_lists_prefetch(backend):
    await library()
    with override_settings(FASTDRF={"SERIALIZER_BACKEND": backend}, AIODRF={}):
        instances = [author async for author in Author.objects.order_by("name")]
        data = await aio.data(StaticAuthorSerializer(instances, many=True))
    assert data == [{"name": "Ada"}, {"name": "Bo"}, {"name": "Cyd"}]
    assert prefetches == [["Ada", "Bo", "Cyd"]]
