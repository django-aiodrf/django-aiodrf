"""Local development settings; not a deployment configuration."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = "local-example-only-do-not-deploy"  # noqa: S105
DEBUG = False
ALLOWED_HOSTS = os.environ.get(
    "EXAMPLE_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver"
).split(",")
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "rest_framework",
    "drf_spectacular",
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
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    },
]
USE_TZ = True
REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "UNAUTHENTICATED_USER": None,
}
MAILERS = {"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}}
AIODRF = {}
SPECTACULAR_SETTINGS = {
    "TITLE": "Policy and body-query API",
    "VERSION": "1.0.0",
    "PREPROCESSING_HOOKS": [
        "aiodrf.contrib.spectacular.hooks.preprocess_exclude_query_method",
    ],
}
INSTALLED_APPS += ["django.contrib.sessions", "rest_framework.authtoken"]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
]
REST_FRAMEWORK["UNAUTHENTICATED_USER"] = "django.contrib.auth.models.AnonymousUser"
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
