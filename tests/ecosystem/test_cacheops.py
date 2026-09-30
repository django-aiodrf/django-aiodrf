"""
django-cacheops caches querysets in Redis and invalidates them from model
signals, queueing the invalidation of a transaction until it commits. aiodrf
reads and writes in worker threads, the save in its own transaction
(``ATOMIC_SAVE``): a stale answer after a write, or a rolled-back write in the
cache, would show here.

Redis db 12 of ``tests/services/compose.yaml`` (6380); the tests flush that
database only.
"""

from cacheops.redis import redis_client
from cacheops.signals import cache_read
from django.test import TransactionTestCase, override_settings
from django.urls import include, path
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.routers import SimpleRouter

from aiodrf import viewsets
from aiodrf.response import Response
from aiodrf.test import AsyncAPIClient, count_hops
from aiodrf.views import APIView
from tests.base import AsyncTransport, SyncTransport
from tests.testapp.models import Author


class AuthorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Author
        fields = ["id", "name"]

    def update(self, instance, validated_data):
        author = super().update(instance, validated_data)
        if author.name == "Veto":
            # The list's query, inside the transaction the save opened.
            assert [author.name for author in Author.objects.order_by("pk")] == ["Veto"]
            raise serializers.ValidationError({"name": "Vetoed after saving."})
        return author


class Authors(viewsets.ModelViewSet):
    queryset = Author.objects.order_by("pk")
    serializer_class = AuthorSerializer
    authentication_classes = []
    permission_classes = [AllowAny]


class Counts(APIView):
    """Django's async ORM (``sync_to_async``) goes through cacheops too."""

    authentication_classes = []
    permission_classes = [AllowAny]

    async def get(self, request, pk):
        author = await Author.objects.aget(pk=pk)
        return Response({"name": author.name, "count": await Author.objects.acount()})


router = SimpleRouter()
router.register("authors", Authors)
urlpatterns = [
    path("", include(router.urls)),
    path("counts/<int:pk>/", Counts.as_view()),
]

cacheops = override_settings(ROOT_URLCONF=__name__, CACHEOPS_ENABLED=True)


class CacheReads:
    """Whether each cacheops read of a request was a hit."""

    def setUp(self):
        super().setUp()
        redis_client.flushdb()
        self.addCleanup(redis_client.flushdb)
        self.reads = []

        def record(sender, hit, **kwargs):
            self.reads.append(hit)

        cache_read.connect(record, weak=False)
        self.addCleanup(cache_read.disconnect, record)

    async def read(self, method, url, **kwargs):
        """``(response, hits)`` of one request."""
        self.reads.clear()
        response = await self.api(method, url, **kwargs)
        return response, list(self.reads)


class _CacheopsTests(CacheReads):
    @cacheops
    async def test_reads_are_cached_and_writes_invalidate(self):
        created = await self.api("post", "/authors/", data={"name": "Ursula"})
        assert created.status_code == 201
        url = f"/authors/{created.data['id']}/"
        for target in ("/authors/", url):
            with self.subTest(url=target):
                first, hits = await self.read("get", target)
                assert hits == [False]
                # ``update()`` sends no signal, so cacheops does not
                # invalidate: the stale answer shows it comes from Redis.
                await Author.objects.aupdate(name="Unseen")
                second, hits = await self.read("get", target)
                assert (second.data, hits) == (first.data, [True])
                await Author.objects.aupdate(name="Ursula")

        assert (
            await self.api("patch", url, data={"name": "Octavia"})
        ).status_code == 200
        response, hits = await self.read("get", "/authors/")
        assert hits == [False]
        assert [author["name"] for author in response.data] == ["Octavia"]
        assert (await self.api("get", url)).data["name"] == "Octavia"

        assert (await self.api("delete", url)).status_code == 204
        response, hits = await self.read("get", "/authors/")
        assert (response.data, hits) == ([], [False])
        assert (await self.api("get", url)).status_code == 404

    @cacheops
    async def test_a_rolled_back_save_leaves_the_cache_correct(self):
        created = await self.api("post", "/authors/", data={"name": "Ursula"})
        url = f"/authors/{created.data['id']}/"
        await self.api("get", url)
        vetoed = await self.api("patch", url, data={"name": "Veto"})
        assert vetoed.status_code == 400, vetoed.data
        assert await Author.objects.aget(name="Ursula")
        # The list read inside the rolled-back transaction was not cached...
        response, hits = await self.read("get", "/authors/")
        assert ([author["name"] for author in response.data], hits) == (
            ["Ursula"],
            [False],
        )
        # ...and the invalidation queued in it was dropped with it.
        response, hits = await self.read("get", url)
        assert (response.data["name"], hits) == ("Ursula", [True])

    @cacheops
    async def test_the_async_orm_in_an_async_handler_is_served_from_the_cache(self):
        author = await Author.objects.acreate(name="Ursula")
        first, hits = await self.read("get", f"/counts/{author.pk}/")
        assert first.data == {"name": "Ursula", "count": 1}
        assert hits == [False, False]
        await Author.objects.aupdate(name="Unseen")
        second, hits = await self.read("get", f"/counts/{author.pk}/")
        assert (second.data, hits) == (first.data, [True, True])
        # ``acreate`` sends ``post_save``: the count is invalidated; the
        # ``pk=`` read cannot match the new row and stays cached.
        await Author.objects.acreate(name="Octavia")
        third, hits = await self.read("get", f"/counts/{author.pk}/")
        assert (third.data, hits) == ({"name": "Ursula", "count": 2}, [True, False])


# cacheops invalidates when a transaction commits, which ``TestCase``'s
# wrapping transaction never does.
class CacheopsASGITests(AsyncTransport, _CacheopsTests, TransactionTestCase):
    pass


class CacheopsWSGITests(SyncTransport, _CacheopsTests, TransactionTestCase):
    pass


class CacheopsHopTests(CacheReads, TransactionTestCase):
    async def test_caching_adds_no_hop(self):
        client = AsyncAPIClient()
        author = await Author.objects.acreate(name="Ursula")
        url = f"/authors/{author.pk}/"
        requests = [
            ("get", "/authors/", {}),
            ("get", url, {}),
            ("patch", url, {"data": {"name": "Octavia"}}),
            ("post", "/authors/", {"data": {"name": "Ursula"}}),
        ]
        measured = {}
        for enabled in (False, True):
            redis_client.flushdb()
            with override_settings(ROOT_URLCONF=__name__, CACHEOPS_ENABLED=enabled):
                for method, target, kwargs in requests:
                    # Twice: the second read is a cache hit.
                    for attempt in range(2 if method == "get" else 1):
                        with count_hops() as hops:
                            response = await getattr(client, method)(target, **kwargs)
                        assert response.status_code < 300, response.data
                        measured[enabled, method, target, attempt] = hops.count
        assert {key[1:]: count for key, count in measured.items() if key[0]} == {
            key[1:]: count for key, count in measured.items() if not key[0]
        }
        # One hop each: the action's synchronous body (queryset, save,
        # representation) is one ``run_sync`` call whether cacheops answers
        # from Redis or the database does.
        assert set(measured.values()) == {1}, measured
