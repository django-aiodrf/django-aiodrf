# Ecosystem tests

These modules run third-party packages against aiodrf views and, where DRF has
an equivalent, compare them with the same view written for DRF. They run with
`nox -s ecosystem` and the settings in `tests/ecosystem/settings.py`; packages
that need their own versions or services have dedicated sessions. The tested
scenarios are described for users in the
[ecosystem guide](../../docs/guides/ecosystem.md). Every test module is listed
here, and `tests/test_docs.py` checks that none is missing.

| Package | Test module |
| --- | --- |
| drf-spectacular | `tests/test_contrib.py`, `tests/test_schema_serializers.py`, `tests/test_schema_contract.py`, `tests/test_streaming_schema.py` |
| django-filter | `tests/test_generics.py`, `tests/test_execution.py` |
| rest-filters | `ecosystem/test_rest_filters.py` |
| django-async-backend | `tests/async_backend/` (`nox -s native_db`) |
| djangorestframework-simplejwt | `ecosystem/test_simplejwt.py` |
| django-rest-knox | `ecosystem/test_knox.py` |
| django-oauth-toolkit | `ecosystem/test_oauth_toolkit.py` |
| drf-auth-kit | `ecosystem/test_auth_kit.py` |
| dj-rest-auth | `ecosystem/test_dj_rest_auth.py` |
| django-guardian, djangorestframework-guardian | `ecosystem/test_guardian.py` |
| drf-nested-routers | `ecosystem/test_nested_routers.py` |
| drf-standardized-errors | `ecosystem/test_standardized_errors.py` |
| django-cors-headers | `ecosystem/test_cors.py` |
| django-simple-history | `ecosystem/test_simple_history.py` |
| django-axes | `ecosystem/test_axes.py` |
| drf-orjson-renderer | `ecosystem/test_orjson.py` |
| drf-excel | `ecosystem/test_excel.py` |
| django-cachalot | `ecosystem/test_cachalot.py` |
| django-cacheops | `ecosystem/test_cacheops.py` |
| django-cleanup | `ecosystem/test_cleanup.py` |
| django-elasticsearch-dsl | `ecosystem/test_elasticsearch.py`, `ecosystem/test_elasticsearch_live.py` (`nox -s ecosystem_elasticsearch`) |
| django-opensearch-dsl, opensearch-py | `ecosystem/opensearch` (`nox -s ecosystem_opensearch`) |
| django-typer | `ecosystem/test_typer.py` |
| django-redis, redis-py | `tests/test_caches.py` |
| django-valkey, valkey-py | `ecosystem/valkey` (`nox -s ecosystem_valkey`) |
| redis.asyncio backend | `ecosystem/redis` (`nox -s ecosystem_redis`); Sentinel and Cluster in `tests/services/cache-topologies.md` |
| django-zeal | `ecosystem/test_zeal.py` |
| asgi-lifespan, HTTPX | `ecosystem/test_lifespan.py` |
| channels, djangochannelsrestframework | `ecosystem/test_channels.py`, `ecosystem/test_channels_serializers.py` |
| django-health-check | `ecosystem/test_health_check.py` |
| django-idempotency-key | `ecosystem/test_idempotency_key.py` |
| djoser | `ecosystem/test_djoser.py` |
| django-allauth | `ecosystem/test_allauth.py` |
| rules | `ecosystem/test_rules.py` |
| drf-api-logger | `ecosystem/test_api_logger.py` |
| apitally | `ecosystem/test_apitally.py` |
| wireup | `ecosystem/test_wireup.py` |
| django-money | `ecosystem/test_money.py` |
| django-phonenumber-field | `ecosystem/test_phonenumber_field.py` |
| django-taggit | `ecosystem/test_taggit.py` |
| drf-extra-fields | `ecosystem/test_extra_fields.py` |
| django-pydantic-field | `ecosystem/test_pydantic_field.py` |
| drf-writable-nested | `ecosystem/test_writable_nested.py` |
| drf-flex-fields | `ecosystem/test_flex_fields.py` |
| django-polymorphic, django-rest-polymorphic | `ecosystem/test_polymorphic.py` |
| django-restql | `ecosystem/test_restql.py` |
| djangorestframework-dataclasses | `ecosystem/test_dataclasses.py` |
| djangorestframework-jsonapi | `ecosystem/test_jsonapi.py` |
| djangorestframework-camel-case | `ecosystem/test_camel_case.py` |
| nested-multipart-parser | `ecosystem/test_nested_multipart_parser.py` |
| drf-tweaks | `ecosystem/test_drf_tweaks.py` |
| djangorestframework-datatables | `ecosystem/test_datatables.py` |
| drf-restwind | `ecosystem/test_restwind.py` |
| servestatic | `ecosystem/test_servestatic.py` |
| whitenoise | `ecosystem/test_whitenoise.py`, `ecosystem/test_whitenoise_adapter.py` |
| django-silk | `ecosystem/test_silk.py` |
| django-debug-toolbar | `ecosystem/test_debug_toolbar.py` |
| django-structlog | `ecosystem/test_structlog.py` |
| django-log-request-id | `ecosystem/test_log_request_id.py` |
| django-tenants | `ecosystem/tenants/test_tenants.py` (`nox -s ecosystem_tenants`) |
| django-mongodb-backend, django-mongodb-extensions | `ecosystem/mongodb/` (`nox -s ecosystem_mongodb`) |
| django-safedelete | `ecosystem/test_safedelete.py` |
| django-auditlog | `ecosystem/test_auditlog.py` |
| django-prometheus | `ecosystem/prometheus/test_prometheus.py` (`nox -s ecosystem_prometheus`) |
| Schemathesis | `examples/bookshop/tests/test_schema_fuzz.py` (`nox -s example`) |
| sentry-sdk, OpenTelemetry, Celery, django-storages | `tests/integrations/` (`nox -s integrations`) |
