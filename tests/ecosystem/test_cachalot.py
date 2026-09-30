"""
django-cachalot caches querysets and invalidates them on writes. aiodrf reads
and writes from worker threads; a stale list after a create, or a rolled-back
write in the cache, would show here.

cachalot patches the ORM when it starts, from ``CACHALOT_ENABLED`` of that
moment (off in these settings), and does not follow ``override_settings``;
``cachalot_settings.reload()`` fails once cacheops has wrapped ``Atomic`` after
it. ``enabled()`` patches the query compiler the way cachalot does.
"""

from contextlib import contextmanager
from unittest import mock

from asgiref.sync import sync_to_async
from cachalot.monkey_patch import _patch_compiler
from django.core.cache import cache
from django.db import connection
from django.db.models.sql.compiler import SQLCompiler
from django.test import TransactionTestCase, override_settings
from django.urls import include, path
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.routers import SimpleRouter

from aiodrf import viewsets
from aiodrf.test import AsyncAPIClient, count_hops
from tests.base import AsyncTransport, SyncTransport
from tests.testapp.models import Author
from tests.testapp.serializers import AuthorSerializer


class VetoSerializer(AuthorSerializer):
    def update(self, instance, validated_data):
        author = super().update(instance, validated_data)
        if author.name == "Veto":
            # The list's query, inside the transaction the save opened.
            assert [author.name for author in Author.objects.order_by("pk")] == ["Veto"]
            raise serializers.ValidationError({"name": "Vetoed after saving."})
        return author


class Authors(viewsets.ModelViewSet):
    queryset = Author.objects.order_by("pk")
    serializer_class = VetoSerializer
    authentication_classes = []
    permission_classes = [AllowAny]


router = SimpleRouter()
router.register("authors", Authors)
urlpatterns = [path("", include(router.urls))]


@contextmanager
def enabled():
    with (
        override_settings(ROOT_URLCONF=__name__),
        mock.patch.object(
            SQLCompiler, "execute_sql", _patch_compiler(SQLCompiler.execute_sql)
        ),
    ):
        yield


@sync_to_async
def raw_rename(name):
    # On the database driver's connection: cachalot does not see this write,
    # so what still shows the old name was served from the cache.
    connection.ensure_connection()
    connection.connection.execute("UPDATE testapp_author SET name = ?", [name])


class Cachalot:
    def setUp(self):
        super().setUp()
        # ``TransactionTestCase`` empties the tables with SQL cachalot does not
        # see; it caches in the default cache.
        cache.clear()
        self.enterContext(enabled())


class _CachalotTests(Cachalot):
    async def test_lists_are_cached_and_invalidated_by_writes(self):
        assert (await self.api("get", "/authors/")).data == []
        created = await self.api("post", "/authors/", data={"name": "Ursula"})
        assert created.status_code == 201
        names = [author["name"] for author in (await self.api("get", "/authors/")).data]
        assert names == ["Ursula"]
        await raw_rename("Unseen")
        # Served from the cache.
        assert (await self.api("get", "/authors/")).data == [
            {**created.data, "name": "Ursula"}
        ]
        url = f"/authors/{created.data['id']}/"
        assert (
            await self.api("patch", url, data={"name": "Octavia"})
        ).status_code == 200
        assert (await self.api("get", url)).data["name"] == "Octavia"
        assert (await self.api("delete", url)).status_code == 204
        assert (await self.api("get", "/authors/")).data == []

    async def test_a_rolled_back_save_leaves_the_cache_correct(self):
        created = await self.api("post", "/authors/", data={"name": "Ursula"})
        url = f"/authors/{created.data['id']}/"
        assert (await self.api("get", url)).data["name"] == "Ursula"
        vetoed = await self.api("patch", url, data={"name": "Veto"})
        assert vetoed.status_code == 400, vetoed.data
        # Neither the list read inside the rolled-back transaction nor its
        # invalidation outlives it.
        names = [author["name"] for author in (await self.api("get", "/authors/")).data]
        assert names == ["Ursula"]
        await raw_rename("Unseen")
        assert (await self.api("get", url)).data["name"] == "Ursula"


# cachalot invalidates when a transaction commits, which ``TestCase``'s
# wrapping transaction never does.
class CachalotASGITests(AsyncTransport, _CachalotTests, TransactionTestCase):
    pass


class CachalotWSGITests(SyncTransport, _CachalotTests, TransactionTestCase):
    pass


class CachalotHopTests(TransactionTestCase):
    async def test_caching_adds_no_hop(self):
        client = AsyncAPIClient()
        author = await Author.objects.acreate(name="Ursula")
        url = f"/authors/{author.pk}/"
        requests = [
            ("get", "/authors/", {}),
            ("get", "/authors/", {}),  # a cache hit
            ("get", url, {}),
            ("patch", url, {"data": {"name": "Octavia"}}),
            ("post", "/authors/", {"data": {"name": "Ursula"}}),
        ]
        measured = {}
        for on in (False, True):
            cache.clear()
            with enabled() if on else override_settings(ROOT_URLCONF=__name__):
                for number, (method, target, kwargs) in enumerate(requests):
                    with count_hops() as hops:
                        response = await getattr(client, method)(target, **kwargs)
                    assert response.status_code < 300, response.data
                    measured[on, number] = hops.count
        # One hop each, cached or not: the action's synchronous body.
        assert set(measured.values()) == {1}, measured
