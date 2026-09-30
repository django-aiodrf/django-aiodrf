"""Tenant schemas use django-tenants' engine, router and middleware unchanged."""

import os

SECRET_KEY = "local-tenant-example-not-for-deployment"  # noqa: S105
DEBUG = False
USE_TZ = True
ALLOWED_HOSTS = os.environ.get(
    "EXAMPLE_ALLOWED_HOSTS", ".example.test,localhost,127.0.0.1"
).split(",")
ROOT_URLCONF = "project.urls"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
DATABASES = {
    "default": {
        "ENGINE": "django_tenants.postgresql_backend",
        "HOST": os.environ.get("PGHOST", "127.0.0.1"),
        "PORT": os.environ.get("PGPORT", "5432"),
        "NAME": os.environ.get("PGDATABASE", "aiodrf_example_tenants"),
        "USER": os.environ.get("PGUSER", "example"),
        "PASSWORD": os.environ.get("PGPASSWORD", ""),
        "CONN_MAX_AGE": 0,
        "TEST": {"NAME": "test_aiodrf_example_tenants"},
    }
}
DATABASE_ROUTERS = ["django_tenants.routers.TenantSyncRouter"]
SHARED_APPS = [
    "django_tenants",
    "customers",
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "rest_framework",
    "aiodrf",
]
TENANT_APPS = ["django.contrib.contenttypes", "notes"]
INSTALLED_APPS = [*SHARED_APPS, *(app for app in TENANT_APPS if app not in SHARED_APPS)]
TENANT_MODEL = "customers.Customer"
TENANT_DOMAIN_MODEL = "customers.Domain"
MIDDLEWARE = [
    "django_tenants.middleware.main.TenantMainMiddleware",
    "django.middleware.security.SecurityMiddleware",
]
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
}
