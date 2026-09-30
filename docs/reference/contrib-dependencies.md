# Contrib dependencies and upstream documentation

aiodrf documents its adapters and execution boundaries. The linked upstream
manuals define each dependency's configuration, security controls and supported
features. Install only the integrations in use; an optional adapter does not
make the dependency's entire API asynchronous.

Use the documentation version matching the installed package. The
[ecosystem version reference](ecosystem-versions.md) identifies the dependency
profiles, and the [ecosystem guide](../guides/ecosystem.md) records tested
contracts. Inclusion here is not an unrestricted compatibility or support
guarantee. Examples requiring services are in
[ecosystem-services](../../examples/ecosystem-services/README.md).

## Cache and data access

| Integration | Upstream documentation | aiodrf scope |
| --- | --- | --- |
| `contrib.redis` | [redis-py asyncio](https://redis.readthedocs.io/en/stable/examples/asyncio_examples.html), [retry configuration](https://redis.readthedocs.io/en/stable/retry.html) | Native cache operations and pool ownership; [cache guide](../guides/async-cache.md) |
| `contrib.valkey` | [django-valkey async backend](https://django-valkey.readthedocs.io/en/latest/async/configurations/), [valkey-py connections](https://valkey-py.readthedocs.io/en/stable/connections.html), [retries](https://valkey-py.readthedocs.io/en/stable/retry.html) | Instance-owned vendor pools, or the separate native contrib backend; [resilience](../guides/cache-resilience.md) |
| Django cache interoperability | [Django cache framework](https://docs.djangoproject.com/en/stable/topics/cache/), [django-redis](https://github.com/jazzband/django-redis) | Synchronous consumers and async thread-adapted calls; no replacement of vendor plugin APIs |
| `contrib.opensearch` | [django-opensearch-dsl](https://django-opensearch-dsl.readthedocs.io/en/latest/), [opensearch-py](https://opensearch-project.github.io/opensearch-py/) | Document preparation and native client writes; [NoSQL guide](../guides/async-nosql.md) |
| Elasticsearch examples | [django-elasticsearch-dsl](https://django-elasticsearch-dsl.readthedocs.io/en/latest/), [Elastic Python client and integrated DSL](https://www.elastic.co/docs/reference/elasticsearch/clients/python) | Django indexing and separately owned native async clients; not OpenSearch clients |
| `contrib.mongodb` | [Django MongoDB Backend](https://www.mongodb.com/docs/languages/python/django-mongodb/current/), [PyMongo](https://pymongo.readthedocs.io/en/stable/) | Field/schema integration; Django and direct async-driver paths remain distinct |
| MongoDB serializer integration | [MongoDB's DRF integration guide](https://www.mongodb.com/docs/languages/python/django-mongodb/current/integrations/rest-framework/), [django-mongodb-extensions](https://github.com/mongodb-labs/django-mongodb-extensions) | Vendor model serializers and embedded fields alongside the aiodrf MongoDB adapter |
| `contrib.async_backend` | [django-async-backend](https://django-async-backend.readthedocs.io/en/latest/), [Psycopg async operations](https://www.psycopg.org/psycopg3/docs/advanced/async.html) | Explicit native PostgreSQL adapter; [version coupling and limitations](../guides/async-backend.md) |

## Serialization, schemas and filtering

| Integration | Upstream documentation | aiodrf scope |
| --- | --- | --- |
| `contrib.msgspec`, `cache_codecs.MsgspecCodec` | [msgspec](https://msgspec.dev/) | Explicit typed schemas, eligible DRF compilation and typed cache values; [serializer contracts](../guides/msgspec-pydantic.md) |
| `contrib.pydantic`, `cache_codecs.PydanticCodec` | [Pydantic](https://docs.pydantic.dev/latest/) | Explicit models/TypeAdapter, eligible DRF compilation and typed cache values; vendor field hooks remain synchronous |
| `contrib.spectacular` | [drf-spectacular](https://drf-spectacular.readthedocs.io/en/latest/) | Schema extensions, QUERY handling and [streaming schemas](../guides/streaming-schema.md) |
| `contrib.django_filters` | [django-filter](https://django-filter.readthedocs.io/en/stable/) | Awaitable filter backend with the vendor's filtering semantics; [integration](../guides/ecosystem.md#django-filter) |
| rest-filters compatibility | [rest-filters](https://github.com/realsuayip/rest-filters) | Tested vendor backend, not a copied filtering implementation; [integration](../guides/ecosystem.md#rest-filters) |

`contrib.builtin` optimizations and the compiler's eligibility rules are aiodrf
implementations, not vendor features. Their configuration belongs in the
[settings reference](settings.md) and [optimization guide](../guides/serializer-optimization.md).

## Authentication and permissions

| Integration | Upstream documentation | aiodrf scope |
| --- | --- | --- |
| `contrib.simplejwt` | [Simple JWT](https://django-rest-framework-simplejwt.readthedocs.io/en/latest/) | Explicit credential-presence checks avoid unnecessary worker dispatch; token authentication and settings remain upstream |
| `contrib.knox` | [Knox documentation](https://github.com/jazzband/django-rest-knox/tree/develop/docs) | Credential-presence checks; token lookup remains worker-adapted and token expiry remains upstream |
| `contrib.auth_kit` | [DRF Auth Kit](https://drf-auth-kit.readthedocs.io/en/latest/) | Cookie/header presence checks; vendor views and cookie/CSRF policy are not replaced |
| `contrib.permissions` | [Django authentication](https://docs.djangoproject.com/en/stable/topics/auth/), [DRF permissions](https://www.django-rest-framework.org/api-guide/permissions/), [django-guardian](https://django-guardian.readthedocs.io/en/stable/), [rules](https://github.com/dfunckt/django-rules) | Preserves synchronous permission-backend participation; [authentication and permission integration](../guides/ecosystem.md#authentication) |

## Runtime, instrumentation and migration

| Integration | Upstream documentation | aiodrf scope |
| --- | --- | --- |
| `contrib.whitenoise` | [WhiteNoise](https://whitenoise.readthedocs.io/en/stable/), [ServeStatic](https://github.com/Archmonger/ServeStatic) | Explicit dual-mode adapter versus a separate ASGI-oriented option; [static-file guide](../guides/static-files.md) |
| `contrib.opentelemetry` | [OpenTelemetry Python API](https://opentelemetry-python.readthedocs.io/en/latest/), [Django instrumentation](https://opentelemetry-python-contrib.readthedocs.io/en/latest/instrumentation/django/django.html) | Optional spans; SDK/exporter setup, sampling and instrumentation are application choices |
| Tasks extra | [Django Tasks](https://docs.djangoproject.com/en/stable/topics/tasks/), [django-tasks backport](https://github.com/RealOrangeOne/django-tasks) | Backport selection and enqueue contracts; no replacement task queue; [task guide](../guides/tasks.md) |
| ASGI lifespan | [ASGI lifespan specification](https://asgi.readthedocs.io/en/latest/specs/lifespan.html), [asgi-lifespan](https://github.com/florimondmanca/asgi-lifespan), [HTTPX](https://www.python-httpx.org/) | [Managed resources](../guides/lifespan.md); test transport and lifespan ownership are separate |
| `contrib.adrf_compat` | [ADRF](https://github.com/em1208/adrf) | Explicit [migration shim](../guides/migration-from-adrf.md), not a default dependency or a runtime performance extension |
| Codemod extra | [LibCST](https://libcst.readthedocs.io/en/latest/) | Migration source transformations, not runtime request processing |

`contrib.convert` generates schema source from DRF, msgspec or Pydantic
definitions; it is separate from the LibCST migration codemod. See
[management commands](../guides/management-commands.md) and the serializer guide.

Consult [runtime adaptations](runtime-adaptations.md) before selecting an
integration that changes vendor registrations or imports. Default contrib use
does not authorize global Django/DRF patches. Packages tested without an adapter
are listed separately in the [ecosystem catalogue](../guides/ecosystem.md).
