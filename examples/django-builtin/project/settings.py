"""Django built-in services with local-only example defaults."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = "example-development-key-not-for-deployment"  # noqa: S105
DEBUG = False
ALLOWED_HOSTS = os.environ.get(
    "EXAMPLE_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver"
).split(",")
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    "django.contrib.flatpages",
    "django.contrib.redirects",
    "django.contrib.humanize",
    "django.contrib.sitemaps",
    "rest_framework",
    "aiodrf",
    "demo",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.contrib.redirects.middleware.RedirectFallbackMiddleware",
]
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
        "CONN_MAX_AGE": 0,
        # A file, not Django's in-memory test database: Django keeps in-memory
        # SQLite connections open in every thread that used them, and Python
        # 3.13+ warns when a worker thread's connection is collected open.
        "TEST": {"NAME": BASE_DIR / "test.sqlite3"},
    }
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
ROOT_URLCONF = "project.urls"
SITE_ID = 1
USE_TZ = True
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / ".static"
MEDIA_ROOT = BASE_DIR / ".media"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]
MAILERS = {"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}}
LOGIN_REDIRECT_URL = "/session/"
CACHE_BACKENDS = {
    "locmem": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "builtin",
    },
    "dummy": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"},
    "file": {
        "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
        "LOCATION": BASE_DIR / ".cache",
    },
    "database": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "example_cache",
    },
    "redis": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": os.environ.get("EXAMPLE_REDIS_URL", "redis://127.0.0.1:6380/13"),
    },
    "django-redis": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": os.environ.get("EXAMPLE_REDIS_URL", "redis://127.0.0.1:6380/13"),
    },
    "pymemcache": {
        "BACKEND": "django.core.cache.backends.memcached.PyMemcacheCache",
        "LOCATION": os.environ.get("EXAMPLE_MEMCACHED", "127.0.0.1:11211"),
    },
    "pylibmc": {
        "BACKEND": "django.core.cache.backends.memcached.PyLibMCCache",
        "LOCATION": os.environ.get("EXAMPLE_MEMCACHED", "127.0.0.1:11211"),
    },
}
CACHE_PROFILE = os.environ.get("EXAMPLE_CACHE", "locmem")
CACHES = {
    "default": {**CACHE_BACKENDS[CACHE_PROFILE], "KEY_PREFIX": "aiodrf-builtin-example"}
}
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication"
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
}
