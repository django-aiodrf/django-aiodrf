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
    }
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
ROOT_URLCONF = "project.urls"
USE_TZ = True
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "UNAUTHENTICATED_USER": None,
}
MAILERS = {"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}}
AIODRF = {}
INSTALLED_APPS += [
    "rest_framework.authtoken",
    "knox",
    "aiodrf.contrib.simplejwt",
    "aiodrf.contrib.knox",
    "aiodrf.contrib.auth_kit",
]
REST_FRAMEWORK["UNAUTHENTICATED_USER"] = "django.contrib.auth.models.AnonymousUser"
AUTH_KIT = {"AUTH_TYPE": "jwt"}
