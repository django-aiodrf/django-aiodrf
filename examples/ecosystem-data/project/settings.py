"""Local data-integration settings; no external service is required."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = "local-data-example-not-for-production"  # noqa: S105
DEBUG = False
ALLOWED_HOSTS = os.environ.get(
    "EXAMPLE_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver"
).split(",")
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "rest_wind",
    "rest_framework",
    "aiodrf",
    "djmoney",
    "phonenumber_field",
    "taggit",
    "polymorphic",
    "demo",
    "django_cleanup.apps.CleanupSelectedConfig",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
]
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
        "CONN_MAX_AGE": 0,
    }
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
ROOT_URLCONF = "project.urls"
USE_TZ = True
STATIC_URL = "/static/"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / ".media"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": ["django.template.context_processors.request"]
        },
    }
]
DRF_NESTED_MULTIPART_PARSER = {"querydict": False}
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
    "EXCEPTION_HANDLER": "drf_standardized_errors.handler.exception_handler",
}
