# Brokers, storage, search and error reporting

The default project uses in-memory storage and eager Celery tasks. It contacts
no cloud service. The same application can be started with explicit Redis,
S3, Elasticsearch, OpenSearch, Valkey or Sentry configuration; credentials are never embedded.

## Run with Docker

From the repository root:

```console
docker compose -f examples/compose.yaml up --build --wait ecosystem-services celery-worker
docker compose -f examples/compose.yaml exec ecosystem-services python manage.py check
```

The API is available on `http://127.0.0.1:8123`. See
[container setup](../CONTAINERS.md) for port overrides, required services,
optional profiles, tests and data retention. The commands below run the same
application directly with uv.

## Run locally

```console
uv venv
uv pip install --python .venv/bin/python -r pyproject.toml --group test -e ../..
uv run --no-sync python manage.py migrate
uv run --no-sync uvicorn project.asgi:application --host 127.0.0.1 --port 8123
uv run --no-sync pytest -q
curl -H 'Content-Type: application/json' -d '{"title":"Example"}' http://127.0.0.1:8123/jobs/
curl -H 'Content-Type: application/json' -d '{"text":"Local note"}' http://127.0.0.1:8123/storage/
```

Set `EXAMPLE_ALLOWED_HOSTS` for another hostname. Public writes, the development
key and process-local storage are not deployment defaults.

## Celery with a broker

Set `EXAMPLE_CELERY_BROKER` in both the API and worker processes. Its presence
disables eager execution. Use a dedicated Redis database/queue:

```console
EXAMPLE_CELERY_BROKER=redis://127.0.0.1:6380/15 uv run --no-sync celery -A project.celery:app worker --pool=solo --loglevel=INFO
EXAMPLE_CELERY_BROKER=redis://127.0.0.1:6380/15 uv run --no-sync uvicorn project.asgi:application --host 127.0.0.1 --port 8123
```

`perform_create` saves the job inside a transaction and registers publication
with `on_commit`. The synchronous broker call runs in the same worker as that
hook, never on the event loop. The job receives a primary key, not a request
or a model instance. A failed publish after commit does not undo the row; use
an application outbox/retry policy when delivery must be durable. Eager tests
are not broker tests. Real broker, rollback, cancellation and worker cleanup
contracts live in `tests/integrations/test_celery*.py`.

## django-storages and S3

```console
EXAMPLE_S3_BUCKET=your-test-bucket AWS_DEFAULT_REGION=eu-west-1 DJANGO_SETTINGS_MODULE=project.settings_s3 uv run --no-sync uvicorn project.asgi:application --host 127.0.0.1 --port 8123
```

Use the normal AWS credential chain and a restricted test bucket. The storage
view enforces a small text limit, generates the object name and calls Django's
storage API in a thread-sensitive worker. It does not make synchronous boto3
asynchronous, expose bucket URLs, or add credentials to requests.

The default example test writes only to in-memory storage. Offline S3 credential,
access-denied and retry contracts are in `tests/integrations/test_storages.py`.
Real IAM, bucket lifecycle, encryption and proxy limits need operator validation.

## django-elasticsearch-dsl and native async search

Use a dedicated index. This profile does not register automatic indexing signals:
database commits and search publication have different failure semantics.

```console
EXAMPLE_SEARCH_URL=http://127.0.0.1:9200 DJANGO_SETTINGS_MODULE=project.settings_search uv run --no-sync python manage.py search_index --create
EXAMPLE_SEARCH_URL=http://127.0.0.1:9200 DJANGO_SETTINGS_MODULE=project.settings_search uv run --no-sync python manage.py search_index --populate
EXAMPLE_SEARCH_URL=http://127.0.0.1:9200 DJANGO_SETTINGS_MODULE=project.settings_search uv run --no-sync uvicorn project.asgi:application --host 127.0.0.1 --port 8123
curl 'http://127.0.0.1:8123/search/?q=example'
curl 'http://127.0.0.1:8123/search/django/?q=example'
curl -X POST http://127.0.0.1:8123/search/ -H 'Content-Type: application/json' -d '{"title":"Native example","completed":false}'
# Replace 1 with the identifier of a Job created through /jobs/.
curl -X POST http://127.0.0.1:8123/search/django/ -H 'Content-Type: application/json' -d '{"job_id":1}'
```

`EXAMPLE_SEARCH_INDEX` defaults to `aiodrf-example-services`. Do not run the
management commands against a production index. `JobDocument.prepare` contains
a narrow subclass override for the tested django-elasticsearch-dsl 9.0 /
elasticsearch-py 9.4+ prepared-field incompatibility; no global class is modified.
The lifespan context owns the native async client, sets its timeout and closes
it at shutdown. Unconfigured search returns 503 rather than silently succeeding.

`/search/django/` uses the synchronous Django Document API inside an aiodrf
worker. Its POST publishes an existing SQL job. `/search/` uses AsyncSearch and
AsyncDocument directly; its POST creates a search-only document with a generated
ID. Both use the same compatible title/completed mapping for demonstration, but
native IDs do not correspond to SQL primary keys. Do not call `to_queryset()`
on a result containing those documents. Native writes bypass Django signals and
model preparation; they are not an alternative transactionally consistent model
save. Both example writes request `refresh="wait_for"` for immediate visibility.
See [NoSQL access paths](../../docs/guides/async-nosql.md).

## Native async Valkey cache

Use a dedicated local Valkey database. No cache connection is made unless the
environment variable is set:

```console
EXAMPLE_VALKEY_URL=valkey://127.0.0.1:6381/14 uv run --no-sync uvicorn project.asgi:application --host 127.0.0.1 --port 8123
curl -X POST http://127.0.0.1:8123/cache/ -H 'Content-Type: application/json' -d '{"title":"Cached example"}'
curl http://127.0.0.1:8123/cache/
```

The entry expires after 30 seconds. `demo.lifecycle` owns the AsyncValkeyCache
instance, creates clients lazily on first use and closes it at shutdown. Its public
`LifespanConnectionFactory` isolates the pool from the vendor's global URL
registry; a blocking pool permits twenty connections per worker and a two-second
wait. Default Django cache consumers still use the synchronous backend.
See [configuration and vendor boundaries](../../docs/guides/async-cache.md).
The [resilience guide](../../docs/guides/cache-resilience.md) explains native
reconnection and explicit retry settings, including why lost write replies must
not be retried indiscriminately. Official manuals for the selected clients are
listed in [contrib dependencies](../../docs/reference/contrib-dependencies.md).

Select `EXAMPLE_CACHE_BACKEND=redis` with `EXAMPLE_REDIS_URL` for redis.asyncio,
or `EXAMPLE_CACHE_BACKEND=valkey-native` with `EXAMPLE_VALKEY_URL` for the contrib
Valkey adapter. These async-only backends additionally support awaited key/codec
callbacks and topology selection. They are not replacements for vendor hash,
compression or Herd plugins. All profiles reuse one backend per lifespan.

## Native cache middleware

```console
EXAMPLE_CACHE_BACKEND=redis EXAMPLE_REDIS_URL=redis://127.0.0.1:6380/14 DJANGO_SETTINGS_MODULE=project.settings_cache uv run --no-sync uvicorn project.asgi:application --host 127.0.0.1 --port 8123
curl -H 'X-Tenant: one' http://127.0.0.1:8123/cache-page/
curl -H 'X-Tenant: one' http://127.0.0.1:8123/cache-page/
curl -H 'X-Tenant: two' http://127.0.0.1:8123/cache-page/
```

The first two calls share a token; the third uses a separate `Vary` key.
`demo.middleware` selects the cache from composite lifespan resources.
Update middleware is first and fetch is last. The profile intentionally caches
public GET/HEAD responses for 30 seconds, not POST writes. Do not apply it to
private responses without a reviewed authorization and cache-control policy.

With the shared Docker stack:

```console
EXAMPLE_CACHE_BACKEND=valkey-native EXAMPLE_SERVICES_SETTINGS=project.settings_cache docker compose -f examples/compose.yaml up --build --wait ecosystem-services
```

For application data instead of page responses, configure the optional
[`MsgspecCodec` or `PydanticCodec`](../../docs/guides/async-cache.md#awaited-callbacks-and-typed-values)
on a separate alias. The same guide includes Sentinel/Cluster settings and
their non-atomic batch/failover limits. Do not select a typed data codec for
page middleware, which stores complete Django response objects.

## OpenSearch

OpenSearch and Elasticsearch use different clients and document registries. This
profile uses `opensearch-py` and `django-opensearch-dsl`; no anysearch or legacy
standalone DSL dependency is installed. Start a dedicated local server or merge
the supplied Docker overlay from the repository root:

```console
docker compose -f examples/compose.yaml -f examples/ecosystem-services/compose.opensearch.yaml up --build --wait ecosystem-services
docker compose -f examples/compose.yaml -f examples/ecosystem-services/compose.opensearch.yaml exec ecosystem-services python manage.py opensearch index create --force
```

The overlay adds OpenSearch without removing the existing Elasticsearch/cache
services. Security is disabled only for this internal local demonstration service;
no OpenSearch port is published. Treat its volume as disposable example data.
For uv with an existing dedicated server:

```console
EXAMPLE_OPENSEARCH_URL=http://127.0.0.1:17640 DJANGO_SETTINGS_MODULE=project.settings_opensearch uv run --no-sync python manage.py opensearch index create --force
EXAMPLE_OPENSEARCH_URL=http://127.0.0.1:17640 DJANGO_SETTINGS_MODULE=project.settings_opensearch uv run --no-sync uvicorn project.asgi:application --host 127.0.0.1 --port 8123
curl -H 'Content-Type: application/json' -d '{"title":"OpenSearch example"}' http://127.0.0.1:8123/jobs/
curl -H 'Content-Type: application/json' -d '{"job_id":1}' http://127.0.0.1:8123/opensearch/
curl 'http://127.0.0.1:8123/opensearch/?q=example'
```

Use the actual job ID from the first response. The POST loads a Django model and
uses `AsyncDocumentWriter`: preparation remains in a worker and HTTP uses the
lifespan-owned client. GET serializes an OpenSearch `Search` body and awaits
`client.search`; it never calls the synchronous DSL `execute()` method.
Index publication is explicit, not an automatic save signal or a distributed
transaction. See the [NoSQL guide](../../docs/guides/async-nosql.md#opensearch) for
bulk iteration, failure boundaries, TLS, upstream warnings and standalone tests.

Live service tests are separate from default offline checks:

```console
EXAMPLE_SEARCH_URL=http://127.0.0.1:9200 EXAMPLE_SEARCH_INDEX=aiodrf-example-test-local EXAMPLE_VALKEY_URL=valkey://127.0.0.1:6381/14 DJANGO_SETTINGS_MODULE=project.settings_search uv run --no-sync pytest -q tests/test_nosql_live.py
```

The search test creates and removes only its new `aiodrf-example-test-*` index;
it fails if the index already exists. The cache test writes the example's fixed
key with a short TTL. Do not point these tests at shared or production resources.

## Sentry and OpenTelemetry

Set `EXAMPLE_SENTRY_DSN` only when remote error reporting is intended. Lifespan
initializes the vendor Django integration with personal-data transmission and
tracing disabled, then closes the client at shutdown. Review data scrubbing,
sampling and retention before enabling an external destination. The example
works without contacting Sentry.

For spans of aiodrf's request phases, see the
[telemetry example](../telemetry/README.md). The OpenTelemetry Django and ASGI
instrumentations are alternative ways to instrument whole requests: enable only
one of them, even when both are installed, to avoid duplicate server spans.
These instrumentations may wrap framework methods; aiodrf itself installs no
wrappers.
