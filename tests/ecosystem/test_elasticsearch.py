"""
django-elasticsearch-dsl indexes a model from its ``post_save`` and
``post_delete`` signals with the synchronous client: a ``_bulk`` request per
write. aiodrf saves in a worker thread, so the request runs there, in the
save's hop. A search built in a synchronous hook (``get_queryset``,
``filter_queryset``) runs in the hop too.

The synchronous client blocks whatever thread calls it: ``Document.search()
.execute()`` written directly in an ``async def`` handler blocks the event
loop. There, use elasticsearch-py's async API, ``AsyncSearch`` with a client
from ``elasticsearch.dsl.async_connections`` (``elasticsearch[async]``).

The client talks to fake nodes that record each request and the thread it
came from; ``test_elasticsearch_live.py`` runs against a real server.
"""

import asyncio
import json

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import include, path
from elastic_transport import ApiResponseMeta, BaseAsyncNode, BaseNode, HttpHeaders
from elastic_transport._node import NodeApiResponse
from elasticsearch.dsl import AsyncSearch, async_connections, connections
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.routers import SimpleRouter

from aiodrf import generics, viewsets
from aiodrf.response import Response
from aiodrf.test import AsyncAPIClient, count_hops
from aiodrf.views import APIView
from tests.base import both_transports
from tests.ecosystem.documents import AuthorDocument
from tests.testapp.models import Author


class AuthorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Author
        fields = ["id", "name"]

    def create(self, validated_data):
        author = super().create(validated_data)
        if author.name == "Veto":
            raise serializers.ValidationError({"name": "Vetoed after saving."})
        return author


class Policies:
    authentication_classes = []
    permission_classes = [AllowAny]


class Authors(Policies, viewsets.ModelViewSet):
    queryset = Author.objects.order_by("pk")
    serializer_class = AuthorSerializer


class SearchAuthors(Policies, generics.ListAPIView):
    serializer_class = AuthorSerializer

    def get_queryset(self):
        # Synchronous: aiodrf calls it in its worker thread.
        search = AuthorDocument.search().query(
            "match", name=self.request.query_params["q"]
        )
        return search.to_queryset()


class AsyncSearchAuthors(Policies, APIView):
    async def get(self, request):
        search = AsyncSearch(index=AuthorDocument._index._name)
        response = await search.query("match", name=request.query_params["q"]).execute()
        return Response([hit.name for hit in response])


class LoopSearchAuthors(Policies, APIView):
    async def get(self, request):
        # What not to do: the synchronous client on the event loop.
        response = (
            AuthorDocument.search()
            .query("match", name=request.query_params["q"])
            .execute()
        )
        return Response([hit.meta.id for hit in response])


router = SimpleRouter()
router.register("authors", Authors)
urlpatterns = [
    path("", include(router.urls)),
    path("search/", SearchAuthors.as_view()),
    path("async-search/", AsyncSearchAuthors.as_view()),
    path("loop-search/", LoopSearchAuthors.as_view()),
]


def on_the_loop():
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


class Recording:
    """Records ``(method, target, body, on_the_loop)``; answers ``_bulk`` and ``_search``."""

    requests = []
    hits = []

    def answer(self, method, target, body):
        type(self).requests.append((method, target, body, on_the_loop()))
        if target.startswith("/_bulk"):
            items = [
                {op: {"_id": pk, "status": 200}} for op, pk, _ in bulk_actions(body)
            ]
            payload = {"took": 1, "errors": False, "items": items}
        else:
            payload = {
                "took": 1,
                "timed_out": False,
                "_shards": {"total": 1, "successful": 1, "skipped": 0, "failed": 0},
                "hits": {
                    "total": {"value": len(self.hits), "relation": "eq"},
                    "max_score": 1.0,
                    "hits": [
                        {
                            "_index": "aiodrf-authors",
                            "_id": str(pk),
                            "_score": 1.0,
                            "_source": {"name": name},
                        }
                        for pk, name in self.hits
                    ],
                },
            }
        headers = HttpHeaders(
            {"x-elastic-product": "Elasticsearch", "content-type": "application/json"}
        )
        meta = ApiResponseMeta(
            status=200,
            http_version="1.1",
            headers=headers,
            duration=0.0,
            node=self.config,
        )
        return NodeApiResponse(meta, json.dumps(payload).encode())


class FakeNode(Recording, BaseNode):
    def perform_request(
        self, method, target, body=None, headers=None, request_timeout=None
    ):
        return self.answer(method, target, body)


class AsyncFakeNode(Recording, BaseAsyncNode):
    async def perform_request(
        self, method, target, body=None, headers=None, request_timeout=None
    ):
        return self.answer(method, target, body)

    async def close(self):
        pass


def bulk_actions(body):
    """``[(action, id, source)]`` of a ``_bulk`` body."""
    lines = [json.loads(line) for line in body.decode().splitlines()]
    actions = []
    while lines:
        ((op, meta),) = lines.pop(0).items()
        source = None if op == "delete" else lines.pop(0)
        actions.append((op, meta["_id"], source))
    return actions


class FakeElasticsearch:
    @classmethod
    def setUpClass(cls):
        fake = {"hosts": "http://elasticsearch.invalid:9200"}
        connections.configure(default={**fake, "node_class": FakeNode})
        async_connections.configure(default={**fake, "node_class": AsyncFakeNode})
        cls.addClassCleanup(connections.configure, **settings.ELASTICSEARCH_DSL)
        cls.addClassCleanup(async_connections.configure)
        cls.enterClassContext(
            override_settings(ROOT_URLCONF=__name__, ELASTICSEARCH_DSL_AUTOSYNC=True)
        )
        super().setUpClass()

    def setUp(self):
        super().setUp()
        Recording.requests = []
        Recording.hits = []


@both_transports
class _IndexingTests(FakeElasticsearch):
    async def test_writes_are_indexed_from_the_worker_thread(self):
        created = await self.api("post", "/authors/", data={"name": "Ursula"})
        pk = created.data["id"]
        url = f"/authors/{pk}/"
        assert (
            await self.api("patch", url, data={"name": "Octavia"})
        ).status_code == 200
        assert (await self.api("delete", url)).status_code == 204
        assert [
            (method, target, bulk_actions(body), loop)
            for method, target, body, loop in Recording.requests
        ] == [
            ("PUT", "/_bulk?refresh=true", [("index", pk, {"name": "Ursula"})], False),
            ("PUT", "/_bulk?refresh=true", [("index", pk, {"name": "Octavia"})], False),
            ("PUT", "/_bulk?refresh=true", [("delete", pk, None)], False),
        ]

    async def test_a_rolled_back_create_is_indexed_anyway(self):
        # django-elasticsearch-dsl indexes on ``post_save``, not on commit:
        # like DRF, the row of a failed save is indexed.
        response = await self.api("post", "/authors/", data={"name": "Veto"})
        assert response.status_code == 400, response.data
        assert not await Author.objects.aexists()
        [(_, _, body, _)] = Recording.requests
        assert [op for op, _, _ in bulk_actions(body)] == ["index"]

    async def test_a_search_in_get_queryset_runs_in_the_hop(self):
        ursula = await Author.objects.acreate(name="Ursula")
        await Author.objects.acreate(name="Octavia")
        Recording.requests, Recording.hits = [], [(ursula.pk, "Ursula")]
        response = await self.api("get", "/search/", data={"q": "ursula"})
        assert response.data == [{"id": ursula.pk, "name": "Ursula"}]
        [(method, target, body, loop)] = Recording.requests
        assert (method, target, loop) == ("POST", "/aiodrf-authors/_search", False)
        assert json.loads(body)["query"] == {"match": {"name": "ursula"}}

    async def test_async_search_in_an_async_handler_runs_on_the_loop(self):
        Recording.hits = [(1, "Ursula")]
        response = await self.api("get", "/async-search/", data={"q": "ursula"})
        assert response.data == ["Ursula"]
        [(method, target, _, loop)] = Recording.requests
        # The async client awaits its node on the loop, without blocking it.
        assert (method, target, loop) == ("POST", "/aiodrf-authors/_search", True)


class LoopTests(FakeElasticsearch, TestCase):
    async def test_the_synchronous_client_in_an_async_handler_blocks_the_loop(self):
        Recording.hits = [(1, "Ursula")]
        response = await AsyncAPIClient().get("/loop-search/", {"q": "ursula"})
        assert response.data == ["1"]
        [(_, _, _, loop)] = Recording.requests
        assert loop


class HopTests(FakeElasticsearch, TestCase):
    async def test_indexing_adds_no_hop(self):
        client = AsyncAPIClient()
        measured = {}
        for autosync in (False, True):
            with override_settings(ELASTICSEARCH_DSL_AUTOSYNC=autosync):
                with count_hops() as hops:
                    created = await client.post("/authors/", {"name": "Ursula"})
                measured[autosync, "post"] = hops.count
                url = f"/authors/{created.data['id']}/"
                with count_hops() as hops:
                    await client.patch(url, {"name": "Octavia"})
                measured[autosync, "patch"] = hops.count
                with count_hops() as hops:
                    await client.delete(url)
                measured[autosync, "delete"] = hops.count
        assert len(Recording.requests) == 3
        # One hop each: the ``_bulk`` request runs in the signal handler,
        # inside the hop that saved or deleted.
        assert set(measured.values()) == {1}, measured

    async def test_a_search_in_get_queryset_adds_no_hop(self):
        with count_hops() as hops:
            response = await AsyncAPIClient().get("/search/", {"q": "ursula"})
        assert response.data == []
        # The search, the query and the representation share the list's hop.
        assert hops.calls == ["ListModelMixin._list"]
