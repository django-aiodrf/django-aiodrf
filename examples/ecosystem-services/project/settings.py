"""Service integrations default to local storage and eager task execution."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = "local-service-example-do-not-deploy"  # noqa: S105
DEBUG = False
ALLOWED_HOSTS = os.environ.get(
    "EXAMPLE_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver"
).split(",")
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "rest_framework",
    "aiodrf",
    "demo",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("EXAMPLE_DB_PATH", BASE_DIR / "db.sqlite3"),
        "CONN_MAX_AGE": 0,
    }
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
ROOT_URLCONF = "project.urls"
USE_TZ = True
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
}
STORAGES = {"default": {"BACKEND": "django.core.files.storage.InMemoryStorage"}}
CELERY_BROKER_URL = os.environ.get("EXAMPLE_CELERY_BROKER", "memory://")
CELERY_TASK_ALWAYS_EAGER = "EXAMPLE_CELERY_BROKER" not in os.environ
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_TASK_IGNORE_RESULT = True
CELERY_TASK_DEFAULT_QUEUE = "aiodrf-example-services"
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = False
AIODRF = {"LIFESPAN": "demo.lifecycle.lifespan"}
EXAMPLE_SEARCH_URL = os.environ.get("EXAMPLE_SEARCH_URL")
EXAMPLE_OPENSEARCH_URL = os.environ.get("EXAMPLE_OPENSEARCH_URL")
EXAMPLE_OPENSEARCH_INDEX = os.environ.get(
    "EXAMPLE_OPENSEARCH_INDEX", "aiodrf-example-jobs"
)
EXAMPLE_SEARCH_INDEX = os.environ.get("EXAMPLE_SEARCH_INDEX", "aiodrf-example-services")
EXAMPLE_VALKEY_URL = os.environ.get("EXAMPLE_VALKEY_URL")
EXAMPLE_REDIS_URL = os.environ.get("EXAMPLE_REDIS_URL")
EXAMPLE_CACHE_BACKEND = os.environ.get("EXAMPLE_CACHE_BACKEND", "valkey")
if EXAMPLE_CACHE_BACKEND not in {"valkey", "valkey-native", "redis"}:
    raise ValueError("EXAMPLE_CACHE_BACKEND must be valkey, valkey-native or redis")
EXAMPLE_CACHE_URL = (
    EXAMPLE_REDIS_URL if EXAMPLE_CACHE_BACKEND == "redis" else EXAMPLE_VALKEY_URL
)
CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
    "native": {
        "BACKEND": "django_valkey.async_cache.cache.AsyncValkeyCache",
        "LOCATION": EXAMPLE_VALKEY_URL or "valkey://127.0.0.1:6381/14",
        "KEY_PREFIX": "aiodrf-services-example",
        "OPTIONS": {
            "CONNECTION_FACTORY": "aiodrf.contrib.valkey.LifespanConnectionFactory",
            "CONNECTION_POOL_CLASS": "valkey.asyncio.connection.BlockingConnectionPool",
            "CONNECTION_POOL_KWARGS": {"max_connections": 20, "timeout": 2},
            "SOCKET_CONNECT_TIMEOUT": 2,
            "SOCKET_TIMEOUT": 2,
            "IGNORE_EXCEPTIONS": False,
            "CLOSE_CONNECTION": True,
        },
    },
}
if EXAMPLE_CACHE_BACKEND in {"redis", "valkey-native"}:
    CACHES["native"] = {
        "BACKEND": "aiodrf.contrib.redis.AsyncRedisCache"
        if EXAMPLE_CACHE_BACKEND == "redis"
        else "aiodrf.contrib.valkey.AsyncValkeyCache",
        "LOCATION": EXAMPLE_CACHE_URL or "redis://127.0.0.1:6380/14",
        "KEY_PREFIX": "aiodrf-services-example",
        "OPTIONS": {
            "socket_connect_timeout": 2,
            "socket_timeout": 2,
            "async_pool_kwargs": {"max_connections": 20, "timeout": 2},
        },
    }
