# Async NoSQL: MongoDB, Elasticsearch and OpenSearch

Choose an access path according to the contracts your application needs.
Django integrations retain supported model, serializer and management-command
behavior. Direct async clients provide native network I/O, but the application
must implement the corresponding data-access rules.

This guide covers both paths, their configuration, resource ownership and
failure boundaries. The runnable MongoDB and services examples use the same
patterns; neither requires a process-wide patch.

## Choosing an access path

| Path | Network execution | Retained integration |
| --- | --- | --- |
| `django-mongodb-backend` | Django's awaitable QuerySet methods delegate to synchronous database work | Models, routers, supported migrations, queryset filtering and model serializers |
| `pymongo.AsyncMongoClient` | Native async PyMongo transport | Explicit collections, queries, indexes and serialization |
| `django-elasticsearch-dsl.Document` | Synchronous operations, executed in a worker | Django model preparation, registry, index commands and optional indexing signals |
| `elasticsearch.dsl.AsyncSearch` / `AsyncDocument` | Native async Elasticsearch transport | Explicit mappings, queries and client lifecycle |
| `django-opensearch-dsl.Document` | Synchronous model preparation and indexing | OpenSearch mappings, registry, commands and optional signals |
| `AsyncDocumentWriter` + `opensearchpy.AsyncOpenSearch` | Model preparation in a worker; native async HTTP | Explicit Django-document publication and native OpenSearch search/bulk |

These distinctions describe the tested versions, listed in the
[ecosystem guide](ecosystem.md); future releases of these packages may differ. Django's standard async ORM API is not a native-driver
selection mechanism. A zero aiodrf hop count also does not mean a thread-free
request: Django and third-party packages can perform their own adaptation.

Use a Django integration when model behavior, queryset filters or third-party
model integrations are central to the endpoint. Use a direct client when the
endpoint is a document/search service and its query, representation and
authorization rules are explicit. Both can run in one project.

## MongoDB with Django models

### Installation and database configuration

Match the Django and django-mongodb-backend release families. The example uses
Django 5.2 and the corresponding backend; the ecosystem suite also exercises
the documented newer combination. Install the serializer extension separately:

```console
uv pip install "Django>=5.2,<5.3" "django-mongodb-backend>=5.2,<5.3" \
  "django-mongodb-extensions[rest-framework]>=0.3,<0.4"
```

For a minimal application without Django's authentication/admin models:

```python
# project/settings.py
import os

INSTALLED_APPS = [
    "rest_framework",
    "aiodrf",
    "aiodrf.contrib.mongodb",
    "notes",
]
DATABASES = {
    "default": {
        "ENGINE": "django_mongodb_backend",
        "HOST": os.environ["MONGODB_URL"],
        "NAME": os.environ.get("MONGODB_DATABASE", "notes"),
        "OPTIONS": {
            "serverSelectionTimeoutMS": 5000,
            "connectTimeoutMS": 5000,
            "socketTimeoutMS": 10000,
            "maxPoolSize": 20,
            "waitQueueTimeoutMS": 5000,
        },
    }
}
DEFAULT_AUTO_FIELD = "django_mongodb_backend.fields.ObjectIdAutoField"
```

Use a replica set when writes require transactions. A local URI might be
`mongodb://mongodb-db:27017/?replicaSet=rs0` inside Compose; hostnames advertised
by the replica set must be reachable from the application. Production URI,
TLS and credentials belong to deployment configuration, not source files.

Django contrib applications with database models require the backend's own
app configuration and ObjectId-compatible migrations. Do not replace an existing
SQL engine and expect its integer-key migrations to become MongoDB migrations.

### Model, serializer and viewset

```python
# notes/models.py
from django.db import models


class Note(models.Model):
    title = models.CharField(max_length=100)
```

```python
# notes/views.py
from django_mongodb_extensions.rest_framework import MongoModelSerializer

from aiodrf import serializers, viewsets
from aiodrf.contrib.mongodb.fields import ObjectIdPrimaryKeyRelatedField

from .models import Note


class NoteSerializer(MongoModelSerializer, serializers.ModelSerializer):
    serializer_related_field = ObjectIdPrimaryKeyRelatedField

    class Meta:
        model = Note
        fields = ["id", "title"]


class Notes(viewsets.ModelViewSet):
    queryset = Note.objects.order_by("id")
    serializer_class = NoteSerializer
```

```python
# project/urls.py
from django.urls import include, path
from rest_framework.routers import SimpleRouter

from notes.views import Notes

router = SimpleRouter()
router.register("notes", Notes)

urlpatterns = [path("", include(router.urls))]
```

`MongoModelSerializer` maps ObjectId, embedded-model and array fields instead
of treating ObjectId primary keys as integers. The optional relation field
above renders related ObjectIds as strings; it does not replace the vendor
serializer or modify any global DRF mapping. In the tested extension version,
relation fields otherwise retain DRF's raw-primary-key representation. With
`aiodrf.contrib.mongodb` installed, `SERIALIZER_BACKEND` compiles
django-mongodb-extensions' `ObjectIdField` and this relation field; embedded
models and arrays stay on DRF.

For a particular relation, DRF's `pk_field` is another option:

```python
from django_mongodb_extensions.rest_framework import ObjectIdField


class Meta:
    model = Book
    fields = ["id", "title", "author", "tags"]
    extra_kwargs = {
        "author": {"pk_field": ObjectIdField()},
        "tags": {"pk_field": ObjectIdField()},
    }
```

This is the `Meta` declaration of a `MongoModelSerializer` for an application's
existing `Book` model. Use string URL parameters for ObjectIds. Tested generic
views return 404 for malformed or missing object keys, and 400 for invalid
related keys supplied in request data.

An async handler can use the backend's supported QuerySet methods:

```python
note = await Note.objects.aget(pk=identifier)
titles = [
    title
    async for title in Note.objects.order_by("id").values_list("title", flat=True)[:20]
]
```

These calls preserve the backend's ORM behavior and worker adaptation. They do
not create or use an `AsyncMongoClient`.

### Transactions

The tested MongoDB backend treats Django's `transaction.atomic()` as a no-op.
Its own `django_mongodb_backend.transaction.atomic()` provides transactions
on supported replica sets and sharded clusters.

Adding `aiodrf.contrib.mongodb` makes aiodrf's default `ATOMIC_SAVE` operation
select that transaction implementation when the connection supports it. On a
standalone server, the adapter retains the backend's nontransactional behavior;
it cannot manufacture rollback support. Without the contrib, the compatibility
check reports `aiodrf.W007` for a MongoDB database with `ATOMIC_SAVE` enabled.

`ATOMIC_SAVE` covers the default synchronous save, including its related writes.
It does not wrap an entire request, a custom async save hook or direct PyMongo
operations. For an application-owned ORM transaction, keep the complete block
in one synchronous function:

```python
from django_mongodb_backend import transaction

from aiodrf.utils import run_sync


def rename_note(identifier, title):
    with transaction.atomic(using="default"):
        note = Note.objects.get(pk=identifier)
        note.title = title
        note.save(update_fields=["title"])
        return note.pk


# Inside an async handler:
identifier = await run_sync(rename_note)(identifier, validated_title)
```

The backend has no savepoints; nested blocks do not provide independent rollback
boundaries. The contrib uses the backend's transaction-capability property,
covered by the integration tests. Revalidate this boundary when upgrading it.

### ORM and serializer limitations

- Many-to-many `prefetch_related()` is unsupported by the tested backend.
  aiodrf's automatic prefetch inference omits those lookups; relation reads may
  therefore execute per object. Explicit `Prefetch` declarations remain the
  application's responsibility and can raise `NotSupportedError`.
- Unsupported ObjectId, relation, embedded-model and array compiler shapes retain
  DRF serialization or raise under an explicit error fallback policy. Inspect
  them with `manage.py aiodrf_inspect_serializers`; a native client does not
  make these fields eligible for compilation.
- `select_for_update()`, raw SQL, cross-collection mutations and Django's
  database cache do not have the same support as a PostgreSQL backend.
  Check the vendor compatibility list for the selected release.
- MongoDB stores datetimes at millisecond precision. Round-trip tests must
  reflect that precision instead of promising Python microsecond preservation.

## Native async PyMongo

### Client lifecycle

Install PyMongo with its async API. Create a client per worker lifespan,
not at import time or for every request:

```python
# project/lifecycle.py
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from django.conf import settings
from pymongo import AsyncMongoClient


@dataclass(frozen=True, slots=True)
class MongoResources:
    client: AsyncMongoClient
    database: str


@asynccontextmanager
async def lifespan() -> AsyncGenerator[MongoResources, None]:
    async with AsyncMongoClient(
        settings.MONGODB_URL,
        maxPoolSize=20,
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
        socketTimeoutMS=10000,
        waitQueueTimeoutMS=5000,
        tz_aware=True,
    ) as client:
        await client.admin.command("ping")
        yield MongoResources(client, settings.MONGODB_DATABASE)
```

```python
# project/settings.py
MONGODB_URL = os.environ["MONGODB_URL"]
MONGODB_DATABASE = os.environ.get("MONGODB_DATABASE", "notes")
DJANGO_LIFESPAN = "project.lifecycle.lifespan"
```

```python
# project/asgi.py
import os

from aiodrf.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "project.settings")
application = get_asgi_application()
```

Run the ASGI server with lifespan enabled. A failed startup ping prevents this
resource-dependent application from accepting requests. If availability is
optional, define a separate readiness/error policy rather than silently
substituting empty data.

Pool limits are per client and typically per server: worker count and topology
increase the total connection budget. Twenty is an example bound, not an optimal
universal value. Keep the client on its owning event loop, bound concurrent work,
and allow lifespan shutdown to close it. Do not call `asyncio.run()` per request.

### Validated reads and writes

```python
# notes/native_views.py
from aiodrf import serializers
from aiodrf_asgi_lifespan.asgi import get_lifespan_state
from aiodrf.response import Response
from aiodrf.views import APIView

from project.lifecycle import MongoResources


class NoteInput(serializers.Serializer):
    title = serializers.CharField(max_length=100)


class NativeNotes(APIView):
    async def get(self, request):
        resources = get_lifespan_state(request, MongoResources)
        collection = resources.client[resources.database]["notes_note"]
        async with collection.find({}, {"title": 1}).sort("_id", 1).limit(20) as cursor:
            rows = [
                {"id": str(row["_id"]), "title": row["title"]} async for row in cursor
            ]
        return Response(rows)

    async def post(self, request):
        serializer = NoteInput(data=await request.adata())
        await serializer.ais_valid(raise_exception=True)
        resources = get_lifespan_state(request, MongoResources)
        collection = resources.client[resources.database]["notes_note"]
        result = await collection.insert_one(dict(serializer.validated_data))
        return Response(
            {"id": str(result.inserted_id), **serializer.validated_data},
            status=201,
        )
```

The collection name must match the model's `db_table` if both paths intentionally
share documents. Direct writes bypass model `save()`, field preparation,
Django signals, database routers and ORM transaction handling. Defaults and
constraints needed on that path must be enforced explicitly.

`find()` returns a cursor without an await. Aggregation starts differently:

```python
cursor = await collection.aggregate(
    [{"$group": {"_id": None, "count": {"$sum": 1}}}],
    maxTimeMS=2000,
)
async with cursor:
    counts = [row async for row in cursor]
```

Bound result size and close cursors when iteration ends early. Do not accept
arbitrary query operators, aggregation stages or projection documents from
untrusted request bodies. Derive tenant scope from authenticated application
state and combine it with an allowlisted query.

For direct-driver transactions, the async session belongs to the same client
and loop. In the tested PyMongo API, session creation is synchronous while
starting the transaction is awaited:

```python
async with resources.client.start_session() as session:
    async with await session.start_transaction():
        await collection.insert_one({"title": "First"}, session=session)
        await collection.insert_one({"title": "Second"}, session=session)
```

Do not issue parallel operations on one session or mix it with an ORM-owned
session. A write timeout or cancellation may leave the commit outcome unknown;
automatic endpoint retries require idempotency and a defined retry policy.

### BSON representation

Return API primitives instead of raw BSON documents:

| BSON/Python value | API representation |
| --- | --- |
| `ObjectId` | Explicit string identifier |
| `bson.Int64` | `int(value)`, with a documented client-side integer range |
| `Decimal128` | Convert to Decimal, then use an explicitly configured DRF DecimalField |
| Datetime | Define timezone and output format; account for millisecond storage precision |
| Binary | Define a base64 or download contract; do not depend on renderer-specific defaults |

A normal serializer can implement the output boundary. Do not assume msgspec,
Pydantic and DRF encode every BSON extension identically. The native path retains
view authentication, permission and throttle hooks, but does not automatically
inherit ModelViewSet queryset scoping, object permissions, pagination or filters.

## Elasticsearch with Django documents

Install the Django integration and a compatible Elasticsearch client. Configure
the connection and index in settings, then create/populate the index through
management commands outside request handling.

```python
ELASTICSEARCH_DSL = {
    "default": {
        "hosts": [os.environ["ELASTICSEARCH_URL"]],
        "request_timeout": 5,
        "connections_per_node": 20,
        "max_retries": 0,
    }
}
ELASTICSEARCH_DSL_AUTOSYNC = False
ELASTICSEARCH_DSL_AUTO_REFRESH = False
```

A registered `Document` defines the model mapping and preparation hooks.
Its search and publication methods are synchronous. Keep the entire operation
inside a synchronous aiodrf handler or one explicit worker function:

```python
from aiodrf.response import Response
from aiodrf.views import APIView

from .documents import JobDocument


class DjangoSearch(APIView):
    def get(self, request):
        term = request.query_params.get("q", "")[:100]
        result = JobDocument.search().query("match", title=term)[:20].execute()
        return Response(
            [{"id": str(hit.meta.id), "title": hit.title} for hit in result]
        )
```

aiodrf runs this synchronous handler in its worker. To publish an existing
model, the worker can call `JobDocument().update(job, refresh="wait_for")`.
Awaiting an unrelated wrapper does not make these synchronous SDK calls safe
to execute directly on the event loop.

The services example includes a scoped `JobDocument.prepare()` override for
the tested django-elasticsearch-dsl 9.0 / newer 9.x client prepared-field
incompatibility. It is tested without modifying the vendor class or registry
globally. Review and remove the workaround when a compatible vendor release
makes it unnecessary.

## Native async Elasticsearch DSL

The current Elasticsearch client exposes the DSL under `elasticsearch.dsl`;
the 9.x example does not require the separate legacy `elasticsearch-dsl`
distribution. Select the client's async extra.

A search-only project can own its client through a separate lifespan module:

```python
# project/search_lifecycle.py
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from django.conf import settings
from elasticsearch import AsyncElasticsearch


@dataclass(frozen=True, slots=True)
class SearchResources:
    client: AsyncElasticsearch


@asynccontextmanager
async def lifespan() -> AsyncGenerator[SearchResources, None]:
    async with AsyncElasticsearch(
        settings.ELASTICSEARCH_URL,
        request_timeout=5,
        connections_per_node=20,
        max_retries=0,
    ) as client:
        await client.info()
        yield SearchResources(client)
```

Set `DJANGO_LIFESPAN = "project.search_lifecycle.lifespan"` for that project.
If an application needs both clients, combine their ownership in one context
manager, for example with `AsyncExitStack`; a project has one configured
lifespan factory, not two competing settings assignments.

```python
from elasticsearch.dsl import AsyncDocument, AsyncSearch, Boolean, Text

from aiodrf_asgi_lifespan.asgi import get_lifespan_state
from aiodrf.response import Response
from aiodrf.views import APIView

from project.search_lifecycle import SearchResources


class SearchNote(AsyncDocument):
    title = Text()
    completed = Boolean()


class NativeSearch(APIView):
    async def get(self, request):
        resources = get_lifespan_state(request, SearchResources)
        term = request.query_params.get("q", "")[:100]
        query = AsyncSearch(using=resources.client, index="notes")
        result = await query.query("match", title=term)[:20].execute()
        return Response(
            [{"id": str(hit.meta.id), "title": hit.title} for hit in result]
        )
```

After validation, a write operation can construct a mapped document:

```python
document = SearchNote(title=validated_title, completed=False)
await document.save(using=resources.client, index="notes", refresh="wait_for")
identifier = str(document.meta.id)
```

Create the mapping before serving writes. Use `async_bulk` with bounded chunks
for batch publication and inspect per-item failures. Do not materialize an
unbounded export or retry a partially successful bulk request blindly.

`refresh="wait_for"` waits for search visibility and adds latency; it does not
make Elasticsearch and SQL one transaction. Choose that policy according to the
endpoint's read-after-write requirements. Keep TLS verification enabled and
provide credentials through deployment configuration.

The Django path publishes an existing SQL model; a native document can have a
generated search-only ID. They do not necessarily perform equivalent work.
Do not call `to_queryset()` on arbitrary native document IDs and assume they
identify Django rows.

## Failure handling and consistency

Treat these as separate application decisions:

- Convert known selection/transport timeouts into a documented availability
  error; record diagnostic detail internally without exposing credentials or
  connection strings. Do not turn every driver exception into an empty result.
- Configure connect, request and queue timeouts independently. A bounded pool
  limits connections, not every pending application task.
- Let cancellation propagate and close cursors/contexts in `finally` or async
  context managers. Do not retry an uncertain write without an idempotency rule.
- SQL transactions do not include MongoDB or Elasticsearch. For durable index
  publication after a model commit, use an outbox and an idempotent consumer.
  A signal or `on_commit()` callback alone is not durable delivery.
- Test read/write contracts, invalid IDs, tenant isolation, timeout handling,
  cancellation, shutdown and the exact stored representation.

Performance comparisons must retain matching documents, projections, indexes,
durability, connection reuse and response shapes. The MongoDB ORM can generate
an aggregation pipeline where direct PyMongo uses `find()`; that difference
includes query generation and server execution, not just thread transitions.

## Runnable examples and tests

The [MongoDB example](../../examples/mongodb/README.md) contains both ORM and
native PyMongo endpoints. The [services example](../../examples/ecosystem-services/README.md)
contains Django Document and native async Elasticsearch paths. Each includes
local commands, tests and container startup instructions.

aiodrf's MongoDB support is tested against both a replica set and a standalone
server. Some MongoDB patch releases refuse to start on affected Linux kernels:
check your server image and host kernel together, and do not disable MongoDB's
start-up safety checks.

### djongo

djongo 1.3.7 declares a Django/PyMongo dependency range older than aiodrf's
supported Django versions, and it does not replace django-mongodb-backend.
Installing it with `--no-deps` does not make it work: every insert fails.

## OpenSearch

### Client and Django integration

OpenSearch uses `opensearch-py`, not Elasticsearch's client. Elasticsearch's DSL
is included in `elasticsearch` from 8.18 onward; new code imports
`elasticsearch.dsl`, not standalone `elasticsearch_dsl`. OpenSearch's DSL lives
in `opensearchpy`. The deprecated standalone `opensearch-dsl` package and the
anysearch abstraction are not dependencies of this integration.

The `opensearch-py` 3.x compatibility matrix includes OpenSearch 2.x/3.x, subject
to removed APIs and server-specific features. This is not Elasticsearch protocol
equivalence. Do not point `elasticsearch.dsl.AsyncSearch` or
`django-elasticsearch-dsl` at OpenSearch. Mapping types, vector queries,
aggregation options, pagination and index settings must target the actual server.

```console
uv pip install "django-aiodrf[opensearch]"
```

```python
INSTALLED_APPS = [
    # Existing Django and project applications...
    "rest_framework",
    "aiodrf",
    "django_opensearch_dsl",
]
OPENSEARCH_DSL = {"default": {"hosts": "https://search.example.org:9200"}}
OPENSEARCH_DSL_AUTOSYNC = False
```

The contrib path uses `django-opensearch-dsl` for model-to-document mapping and
`opensearch-py[async]` for network I/O. Automatic indexing is disabled in this
example so a database save does not also trigger the vendor's synchronous HTTP
signals. Existing applications can retain those signals in synchronous workers;
they are not converted into native async receivers by aiodrf.

### Document mapping and explicit publication

```python
# catalog/documents.py
from django_opensearch_dsl import Document
from django_opensearch_dsl.registries import registry
from .models import Product


@registry.register_document
class ProductDocument(Document):
    class Index:
        name = "products"

    class Django:
        model = Product
        fields = ["title", "price"]
        ignore_signals = True
```

Keep mappings and index creation in deployment/management workflows. A request
handler should not create an index or register document classes. Create one native
client in the ASGI lifespan:

```python
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from django.conf import settings
from opensearchpy import AsyncOpenSearch


@asynccontextmanager
async def lifespan() -> AsyncGenerator[AsyncOpenSearch, None]:
    async with AsyncOpenSearch(
        hosts=[settings.OPENSEARCH_URL],
        verify_certs=True,
        timeout=5,
        maxsize=20,
    ) as client:
        yield client
```

Use a trusted CA and deployment credentials; a local security-disabled Compose
service is not a production configuration. For composite lifespan state, place
the client in a resource dataclass as in the services example.

```python
from aiodrf.contrib.opensearch import AsyncDocumentWriter
from catalog.documents import ProductDocument
from catalog.models import Product

writer = AsyncDocumentWriter(ProductDocument, client=client, index="products")
product = await Product.objects.aget(pk=product_id)
await writer.aindex(product)
await writer.adelete(product)

await writer.abulk(
    Product.objects.order_by("pk").aiterator(chunk_size=500),
    chunk_size=500,
    max_chunk_bytes=5 * 1024 * 1024,
)
```

`AsyncDocumentWriter` exposes `aindex`, `adelete` and `abulk`. It uses the
document's public `generate_id`, `prepare` and `should_index_object` hooks.
Preparation, including deferred fields and lazy relations, runs in a
thread-sensitive worker. Each operation gets a new document instance; the writer
does not retain per-model preparation state or register global clients. A model
without an ID is rejected; an excluded index operation returns `None`. Deletion
does not evaluate field preparation or the indexing predicate.

`abulk` accepts an async iterable and `index`, `create` or `delete` actions. Use
`QuerySet.aiterator()` rather than passing the QuerySet directly: its ordinary
async iterator caches the full result, while synchronous iteration can block.
The native bulk helper controls chunking, retries and item errors. Its default
raises `BulkIndexError`; use its explicit options if the application needs an
error list. Large collected error lists can retain source documents in memory.
The caller owns and closes any input iterator holding external resources.

The writer does not emulate vendor `post_index` signals, related-model
propagation, `update()` overrides, automatic refresh settings or management
commands. Retain vendor commands/signals in workers when those behaviors are
required. Explicit refresh options pass through to the client; `wait_for` is
useful in demonstrations but should not be added to every production write.
For durable SQL-to-search delivery, publish from an outbox consumer after commit.
Neither `on_commit`, async indexing nor a Django transaction makes two services
atomic. Cancellation and transport errors do not imply the remote write failed.

### Native queries with the OpenSearch DSL

OpenSearch's `Search.execute()` is synchronous. Build a query with `Search` and
pass its body to the native client instead:

```python
from opensearchpy import Search

query = Search().query("match", title="django").sort("price")[0:20]
query.aggs.bucket("prices", "stats", field="price")
result = await client.search(index="products", body=query.to_dict())
items = [hit["_source"] for hit in result["hits"]["hits"]]
```

`to_dict()` serializes the request body, not connection selection or all URL
parameters. Pass index, routing, timeout and other transport parameters explicitly
to `client.search()`. Do not call `to_queryset()` in an async handler without a
worker boundary: hydrating Django models adds SQL work and is not native HTTP.

### Tested behaviour

Document hooks, deferred ORM access, bulk chunking, transport errors and
cancellation are tested with the real client, and index creation, mappings,
writes, deletion, aggregations and pagination against OpenSearch 2.19 and 3.7
servers. Plugins, vector search, the authorization of managed services and
cluster failures are not covered.

`opensearch-py` 3.2.0 passes aiohttp's `enable_cleanup_closed` option even on
Python versions where the underlying workaround is unnecessary. Recent aiohttp
therefore emits a specific `DeprecationWarning`. The isolated OpenSearch tests
filter only that warning from `aiohttp.connector`; no application warning filter
or dependency monkeypatch is installed. All other warnings remain failures.

## References

- [Django async execution](https://docs.djangoproject.com/en/stable/topics/async/).
- [MongoDB backend compatibility](https://www.mongodb.com/docs/languages/python/django-mongodb/current/limitations-upcoming/).
- [PyMongo async migration](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/reference/migration/).
- [Elasticsearch async DSL](https://elasticsearch-py.readthedocs.io/en/stable/async_dsl.html).
- [Elasticsearch DSL migration](https://elasticsearch-dsl.readthedocs.io/en/v8.18.0/Changelog.html).
- [OpenSearch Python client compatibility](https://github.com/opensearch-project/opensearch-py/blob/main/COMPATIBILITY.md).
- [OpenSearch DSL migration](https://docs.opensearch.org/latest/clients/python-high-level/).
- [django-opensearch-dsl](https://github.com/codoc-health/django-opensearch-dsl).
