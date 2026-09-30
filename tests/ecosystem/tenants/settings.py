"""
Settings for ``nox -s ecosystem_tenants``: django-tenants on PostgreSQL.

    docker compose -f tests/services/compose.yaml up -d --wait
    DJANGO_SETTINGS_MODULE=tests.ecosystem.tenants.settings pytest tests/ecosystem/tenants

A project of its own rather than the ecosystem settings: django-tenants needs
its database engine, router and middleware for every request, and separates
the apps of the public schema from those of each tenant's schema.
"""

import os

SECRET_KEY = "aiodrf-tenants-tests"
DEBUG = False
USE_TZ = True
TIME_ZONE = "UTC"
ROOT_URLCONF = "tests.ecosystem.tenants.test_tenants"
DEFAULT_AUTO_FIELD = "django.db.models.AutoField"
ALLOWED_HOSTS = [".example.test"]

DATABASES = {
    "default": {
        "ENGINE": "django_tenants.postgresql_backend",
        "NAME": "bench",
        "USER": "bench",
        "PASSWORD": "bench",
        "HOST": "127.0.0.1",
        "PORT": os.environ.get("AIODRF_BENCH_PG_PORT", "55433"),
        "TEST": {"NAME": "test_aiodrf_tenants"},
    }
}
DATABASE_ROUTERS = ["django_tenants.routers.TenantSyncRouter"]

SHARED_APPS = [
    "django_tenants",
    "tests.ecosystem.tenants.customers",
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "rest_framework",
    "aiodrf",
]
TENANT_APPS = ["django.contrib.contenttypes", "tests.ecosystem.tenants.notes"]
INSTALLED_APPS = [*SHARED_APPS, *(app for app in TENANT_APPS if app not in SHARED_APPS)]
TENANT_MODEL = "customers.Customer"
TENANT_DOMAIN_MODEL = "customers.Domain"

MIDDLEWARE = ["django_tenants.middleware.main.TenantMainMiddleware"]

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
}

# ``AIODRF_TEST_PROFILE=tuned``: the benchmark's tuned profile (tests/profiles.py).
from tests.profiles import apply as _apply_profile  # noqa: E402

_apply_profile(globals())
