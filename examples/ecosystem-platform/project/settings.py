"""Operational examples on Django 6.0, matching django-prometheus's supported range."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = "local-platform-example-not-for-deployment"  # noqa: S105
DEBUG = False
ALLOWED_HOSTS = os.environ.get(
    "EXAMPLE_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver"
).split(",")
INSTALLED_APPS = [
    "servestatic",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "rest_framework",
    "channels",
    "aiodrf",
    "demo",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "servestatic.middleware.ServeStaticMiddleware",
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
# Serving app assets without a generated collectstatic directory is a local-only profile.
WHITENOISE_USE_FINDERS = True
SERVESTATIC_USE_FINDERS = True
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": ["django.template.context_processors.request"]
        },
    }
]
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
}
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
