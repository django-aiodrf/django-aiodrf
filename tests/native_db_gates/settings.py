"""
PostgreSQL connection and transaction contracts using django-async-backend.

Run through ``nox -s native_db`` with PostgreSQL from
``tests/services/compose.yaml``.
"""

from tests.settings_postgres import *  # noqa: F403

for _alias in DATABASES.values():  # noqa: F405
    _alias["ENGINE"] = "django_async_backend.db.backends.postgresql"
    _alias["CONN_MAX_AGE"] = 0

INSTALLED_APPS = [*INSTALLED_APPS, "django_async_backend"]  # noqa: F405
