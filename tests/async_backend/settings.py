"""
The test project on PostgreSQL with django-async-backend's engine and app,
for ``aiodrf.contrib.async_backend`` (``nox -s native_db``; PostgreSQL from
``tests/services/compose.yaml``).
"""

import os

from tests.settings_postgres import *  # noqa: F403

for _alias in DATABASES.values():  # noqa: F405
    _alias["ENGINE"] = "django_async_backend.db.backends.postgresql"
    _alias["CONN_MAX_AGE"] = 0

INSTALLED_APPS = [*INSTALLED_APPS, "django_async_backend", "tests.async_backend"]  # noqa: F405
ROOT_URLCONF = "tests.async_backend.urls"

# Synchronous receivers that defer work with ``on_commit`` or their own
# transaction tracking (test_on_commit.py). cacheops is enabled by the tests
# that use Redis; its receivers are connected for every model regardless.
INSTALLED_APPS += ["django_cleanup.apps.CleanupConfig", "cacheops"]
CACHEOPS_ENABLED = False
CACHEOPS_REDIS = "redis://127.0.0.1:{}/14".format(
    os.environ.get("AIODRF_BENCH_REDIS_PORT", "6380")
)
CACHEOPS = {"async_backend_tests.document": {"ops": "all", "timeout": 60}}
