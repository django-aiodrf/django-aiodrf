# MongoDB model serialization

MongoModelSerializer, ObjectId keys and the opt-in transaction adapter, alongside
direct native async PyMongo reads and writes against the same collection.

## Run with Docker

From the repository root:

```console
docker compose -f examples/compose.yaml up --build --wait mongodb
docker compose -f examples/compose.yaml exec mongodb python manage.py check
```

The API is available on `http://127.0.0.1:8116`. See
[container setup](../CONTAINERS.md) for port overrides, required services,
optional profiles, tests and data retention. The commands below run the same
application directly with uv.

## Run

From this directory (Python 3.12+ and uv):

```console
uv venv
uv pip install --python .venv/bin/python -r pyproject.toml --group test -e ../..
uv run --no-sync python manage.py check
uv run --no-sync python manage.py migrate
uv run --no-sync uvicorn project.asgi:application --host 127.0.0.1 --port 8116
```

Change `--host` and `--port` as needed. For another hostname also set
`EXAMPLE_ALLOWED_HOSTS=localhost,127.0.0.1,your-host`. The default is loopback.
This is an independent uv project using the local aiodrf checkout through
`[tool.uv.sources]`; it imports no other example.

Host-based runs require a replica set configured before migration or startup.
The Docker recipe initializes its own single-node replica set automatically.

## Requests

```console
# For a host-based run, configure a dedicated local replica set first.
MONGODB_URL='mongodb://127.0.0.1:27017/?replicaSet=rs0' uv run --no-sync python manage.py migrate
curl -X POST http://127.0.0.1:8116/notes/ -H 'Content-Type: application/json' -d '{"title":"Mongo"}'
curl http://127.0.0.1:8116/notes/
curl -X POST http://127.0.0.1:8116/native-notes/ -H 'Content-Type: application/json' -d '{"title":"Native Mongo"}'
curl http://127.0.0.1:8116/native-notes/
```

## Tests

```console
uv run --no-sync pytest -q
```

Tests exercise the actual ASGI application with lifespan startup/shutdown.
They require the named service and an isolated test database; unavailable services fail, not silently skip.

## Compatibility and limits

`/notes/` uses a Django queryset and MongoModelSerializer. `/native-notes/`
validates a plain serializer, awaits `AsyncMongoClient.insert_one()` and reads
an async cursor. Both return string ObjectIds; native inserts can be retrieved
through `/notes/<id>/`. The native list is limited to twenty rows and ordered
by ObjectId. It does not reproduce the generic viewset's pagination contract.

`demo.lifecycle.lifespan` owns one direct client per worker, with a pool ceiling
of 20 and five-second selection/queue timeouts. aiodrf's ASGI application shares
it through typed lifespan state and closes it at shutdown. The native path
does not call Django model hooks, routers or signals and does not use the
contrib's ORM save transaction. Do not infer transaction or model-validation
parity from these two simple title-only endpoints. See
[NoSQL access paths](../../docs/guides/async-nosql.md) for selection criteria.

MONGODB_URL and MONGODB_DATABASE apply to both migration and server commands. Authentication apps are deliberately excluded because they require backend-specific ObjectId migrations. A standalone MongoDB server does not provide the transaction guarantee of a replica set. ORM calls use the synchronous driver in a worker thread, so their database I/O
is not asynchronous.

The settings use a development secret key and a deliberately simple
permission policy: do not deploy this project as it is.
See the [feature guide](../../docs/guides/async-nosql.md) and the [example catalogue](../README.md).
