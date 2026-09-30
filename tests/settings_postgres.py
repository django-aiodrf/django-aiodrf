"""
Settings for ``nox -s tests_postgres``: the test project on PostgreSQL.

    docker compose -f tests/services/compose.yaml up -d --wait
    DJANGO_SETTINGS_MODULE=tests.settings_postgres pytest --no-migrations tests/postgres

``--no-migrations`` because the test app has none and refers to the user
table: ``migrate --run-syncdb`` creates such tables before it migrates
``auth``, which SQLite tolerates and PostgreSQL does not.
"""

import os

from tests.settings import *  # noqa: F403

_SERVER = {
    "ENGINE": "django.db.backends.postgresql",
    "NAME": "bench",
    "USER": "bench",
    "PASSWORD": "bench",
    "HOST": "127.0.0.1",
    "PORT": os.environ.get("AIODRF_BENCH_PG_PORT", "55433"),
}
DATABASES = {
    "default": {**_SERVER, "TEST": {"NAME": "test_aiodrf"}},
    "other": {**_SERVER, "TEST": {"NAME": "test_aiodrf_other"}},
}
