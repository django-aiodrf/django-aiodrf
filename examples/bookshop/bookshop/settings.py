"""
Settings of the example project: what a project on aiodrf would set.

Everything aiodrf-specific is in ``AIODRF`` and in the ``aiodrf`` app; the rest
is ordinary Django and DRF configuration.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "example-only-not-secret")
DEBUG = os.environ.get("DJANGO_DEBUG") == "1"
ALLOWED_HOSTS = os.environ.get(
    "EXAMPLE_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver"
).split(",")
USE_TZ = True
TIME_ZONE = "UTC"
ROOT_URLCONF = "bookshop.urls"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "rest_framework",
    "rest_framework.authtoken",
    "django_filters",
    "drf_spectacular",
    "aiodrf",
    "catalog",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
        # A file, not SQLite's in-memory default: every ASGI request has a
        # thread and a connection of its own, and Django only closes
        # connections to a file database.
        "TEST": {"NAME": BASE_DIR / "test-db.sqlite3"},
    }
}

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication"
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticatedOrReadOnly"
    ],
    "DEFAULT_FILTER_BACKENDS": ["aiodrf.contrib.django_filters.DjangoFilterBackend"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 10,
    # Counted with the cache's atomic add/incr: concurrent requests cannot
    # all pass the limit.
    "DEFAULT_THROTTLE_CLASSES": [
        "aiodrf.throttling.AnonFixedWindowRateThrottle",
        "aiodrf.throttling.UserFixedWindowRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {"anon": "60/min", "user": "600/min"},
    "DEFAULT_SCHEMA_CLASS": "aiodrf.contrib.spectacular.AutoSchema",
}

SPECTACULAR_SETTINGS = {"TITLE": "Bookshop", "VERSION": "1.0.0"}

AIODRF = {}
DJANGO_LIFESPAN = "bookshop.lifecycle.lifespan"

STOCK_SERVICE_URL = os.environ.get("STOCK_SERVICE_URL", "")
