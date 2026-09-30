"""Real document mapping, worker preparation and native OpenSearch commands."""

import asyncio
import json
import threading
from unittest.mock import AsyncMock

import pytest
from django_opensearch_dsl import Document
from django_opensearch_dsl.registries import DocumentRegistry
from opensearchpy import AsyncOpenSearch, Search
from opensearchpy._async.http_aiohttp import AsyncConnection

from aiodrf.contrib.opensearch import AsyncDocumentWriter
from tests.testapp.models import Author

# opensearch-py 3.2 always passes enable_cleanup_closed to aiohttp. New Python
# versions no longer need that workaround. Keep this vendor warning isolated.
pytestmark = pytest.mark.filterwarnings(
    "ignore:enable_cleanup_closed ignored because.*:DeprecationWarning:aiohttp.connector"
)


@pytest.fixture
def document():
    registry = DocumentRegistry()
    loop_thread = threading.get_ident()

    @registry.register_document
    class AuthorDocument(Document):
        class Index:
            name = "aiodrf-test"

        class Django:
            model = Author
            fields = ["name"]
            ignore_signals = True

        def prepare_name(self, instance):
            assert threading.get_ident() != loop_thread
            return instance.name.upper()

        def should_index_object(self, instance):
            assert threading.get_ident() != loop_thread
            return instance.name != "excluded"

        @classmethod
        def generate_id(cls, instance):
            return f"author-{instance.pk}" if instance.pk else None

    return AuthorDocument


class RecordingConnection(AsyncConnection):
    """Native transport boundary; no server or global connection registration."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.requests = []
        self.closed = False

    async def perform_request(self, method, url, params=None, body=None, **kwargs):
        asyncio.get_running_loop()
        self.requests.append((method, url, body))
        if url == "/_bulk":
            lines = body.decode().splitlines()
            items = []
            while lines:
                metadata = json.loads(lines.pop(0))
                operation = next(iter(metadata))
                items.append(
                    {operation: {"status": 201, "_id": metadata[operation]["_id"]}}
                )
                if operation != "delete":
                    lines.pop(0)
            result = {"errors": False, "items": items}
        elif url.endswith("/_search"):
            result = {"hits": {"total": {"value": 0, "relation": "eq"}, "hits": []}}
        else:
            result = {"result": "deleted" if method == "DELETE" else "created"}
        return 200, {"content-type": "application/json"}, json.dumps(result)

    async def close(self):
        self.closed = True


async def test_document_crud_bulk_and_dsl_use_native_transport(document):
    async with AsyncOpenSearch(
        hosts=["http://localhost:9200"], connection_class=RecordingConnection
    ) as client:
        writer = AsyncDocumentWriter(document, client=client, index="aiodrf-test")
        author = Author(pk=1, name="Ada")
        assert await writer.aindex(author) == {"result": "created"}
        assert await writer.aindex(Author(pk=2, name="excluded")) is None
        assert await writer.adelete(author) == {"result": "deleted"}

        async def authors():
            for pk in range(4):
                yield Author(pk=pk + 1, name="Ada")

        assert await writer.abulk(authors(), chunk_size=2) == (4, [])
        query = Search().query("match", name="ADA")
        result = await client.search(index="aiodrf-test", body=query.to_dict())
        assert result["hits"]["hits"] == []
        connection = client.transport.get_connection()
        assert json.loads(connection.requests[0][2]) == {"name": "ADA"}
        assert (
            len([request for request in connection.requests if request[1] == "/_bulk"])
            == 2
        )
    assert connection.closed


async def test_invalid_inputs_and_transport_errors_are_not_hidden(document):
    client = AsyncMock(spec=AsyncOpenSearch)
    client.index = AsyncMock()
    writer = AsyncDocumentWriter(document, client=client, index="aiodrf-test")
    with pytest.raises(ValueError, match="Save the model"):
        await writer.aindex(Author(name="unsaved"))
    with pytest.raises(TypeError, match="async iterable"):
        await writer.abulk(Author.objects.all())
    with pytest.raises(ValueError, match="action"):
        await writer.abulk([], action="unknown")
    client.index.side_effect = ConnectionError("unavailable")
    with pytest.raises(ConnectionError, match="unavailable"):
        await writer.aindex(Author(pk=1, name="Ada"))
    client.index.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await writer.aindex(Author(pk=1, name="Ada"))
    assert client.index.await_count == 2


@pytest.mark.django_db(transaction=True)
async def test_preparation_can_read_deferred_django_fields(document):
    from asgiref.sync import sync_to_async

    author = await Author.objects.acreate(name="Grace")
    author = await Author.objects.only("pk").aget(pk=author.pk)
    client = AsyncMock(spec=AsyncOpenSearch)
    client.index = AsyncMock(return_value={"result": "created"})
    writer = AsyncDocumentWriter(document, client=client, index="aiodrf-test")
    await writer.aindex(author)
    assert client.index.call_args.kwargs["body"] == {"name": "GRACE"}
    assert await sync_to_async(Author.objects.count)() == 1


async def test_bulk_item_errors_and_cancellation_propagate(document):
    from opensearchpy.helpers import BulkIndexError

    async with AsyncOpenSearch(
        hosts=["http://localhost:9200"], connection_class=RecordingConnection
    ) as client:
        client.bulk = AsyncMock(
            return_value={
                "errors": True,
                "items": [
                    {
                        "index": {
                            "status": 400,
                            "error": {"type": "mapper_parsing_exception"},
                        }
                    }
                ],
            }
        )
        writer = AsyncDocumentWriter(document, client=client, index="aiodrf-test")

        async def authors():
            yield Author(pk=1, name="Ada")

        with pytest.raises(BulkIndexError):
            await writer.abulk(authors())
        client.bulk.side_effect = asyncio.CancelledError
        with pytest.raises(asyncio.CancelledError):
            await writer.abulk(authors())


async def test_a_failed_bulk_closes_the_instances_iterator(document):
    # A QuerySet.aiterator() holds a database cursor until it is closed.
    from opensearchpy.helpers import BulkIndexError

    closed = []

    async def authors():
        try:
            for pk in range(1, 5):
                yield Author(pk=pk, name=f"author {pk}")
        finally:
            closed.append(True)

    async with AsyncOpenSearch(
        hosts=["http://localhost:9200"], connection_class=RecordingConnection
    ) as client:
        client.bulk = AsyncMock(
            return_value={
                "errors": True,
                "items": [{"index": {"status": 400, "error": {"type": "failed"}}}],
            }
        )
        writer = AsyncDocumentWriter(document, client=client, index="aiodrf-test")
        with pytest.raises(BulkIndexError):
            await writer.abulk(authors(), chunk_size=1)
    assert closed == [True]


async def test_live_search_index_mapping_aggregation_pagination_and_bulk(document):
    import os
    from uuid import uuid4

    url = os.getenv("AIODRF_TEST_OPENSEARCH_URL")
    if not url:
        pytest.skip("Set AIODRF_TEST_OPENSEARCH_URL to a dedicated OpenSearch server")
    index = "aiodrf-test-" + uuid4().hex
    async with AsyncOpenSearch(hosts=[url]) as client:
        await client.indices.create(
            index=index,
            body={
                "settings": {"number_of_shards": 1, "number_of_replicas": 0},
                "mappings": {"properties": {"name": {"type": "keyword"}}},
            },
        )
        try:
            writer = AsyncDocumentWriter(document, client=client, index=index)
            await writer.aindex(Author(pk=1, name="Ada"), refresh="wait_for")

            async def authors():
                for pk in (2, 3):
                    yield Author(pk=pk, name="Grace")

            assert await writer.abulk(authors(), chunk_size=1, refresh="wait_for") == (
                2,
                [],
            )
            query = Search().query("match_all").sort("name")[0:2]
            query.aggs.bucket("names", "terms", field="name")
            result = await client.search(index=index, body=query.to_dict())
            assert result["hits"]["total"]["value"] == 3
            assert len(result["hits"]["hits"]) == 2
            assert {
                bucket["key"]: bucket["doc_count"]
                for bucket in result["aggregations"]["names"]["buckets"]
            } == {"ADA": 1, "GRACE": 2}
            await writer.adelete(Author(pk=1), refresh="wait_for")
            assert (await client.count(index=index))["count"] == 2
        finally:
            # Delete only the unique index created by this test.
            await client.indices.delete(index=index)
