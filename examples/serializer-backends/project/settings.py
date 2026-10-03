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
        # A file, not Django's in-memory test database: Django keeps in-memory
        # SQLite connections open in every thread that used them, and Python
        # 3.13+ warns when a worker thread's connection is collected open.
        "TEST": {"NAME": BASE_DIR / "test.sqlite3"},
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
PROFILES = {
    "normal": {},
    "strict-msgspec": {"SERIALIZER_BACKEND": "msgspec"},
    "strict-pydantic": {"SERIALIZER_BACKEND": "pydantic"},
    "tuned": {
        "SERIALIZER_BACKEND": "msgspec",
        "SERIALIZER_BACKEND_PARITY": "fast",
        "CACHE_SERIALIZER_FIELDS": True,
        "BATCH_RELATED_LOOKUPS": True,
    },
}
PROFILE = os.environ.get("AIODRF_EXAMPLE_PROFILE", "normal")
PROFILES["tuned-clone"] = {**PROFILES["tuned"], "FIELD_COPY_MODE": "clone"}
PROFILES["tuned-compiled"] = {**PROFILES["tuned"], "FIELD_COPY_MODE": "compiled"}
# The profiles are django-fastdrf's settings, which aiodrf builds on.
FASTDRF = PROFILES[PROFILE]
