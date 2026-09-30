"""
Settings for ``nox -s ecosystem_mongodb``: django-mongodb-backend and
django-mongodb-extensions on MongoDB.

    docker compose -f tests/services/compose.yaml up -d --wait mongodb
    DJANGO_SETTINGS_MODULE=tests.ecosystem.mongodb.settings pytest tests/ecosystem/mongodb

A project of its own: every model of it lives on MongoDB, whose keys are
``ObjectId``s. ``default`` is a replica set, which transactions need;
``standalone`` is a single server without them.
"""

import os

SECRET_KEY = "aiodrf-mongodb-tests"
DEBUG = False
USE_TZ = True
TIME_ZONE = "UTC"
ROOT_URLCONF = "tests.ecosystem.mongodb.test_views"
DEFAULT_AUTO_FIELD = "django_mongodb_backend.fields.ObjectIdAutoField"

DATABASES = {
    "default": {
        "ENGINE": "django_mongodb_backend",
        "HOST": os.environ.get(
            "AIODRF_TEST_MONGODB_URL",
            "mongodb://127.0.0.1:27117/?directConnection=true",
        ),
        "NAME": "aiodrf",
    },
    "standalone": {
        "ENGINE": "django_mongodb_backend",
        "HOST": os.environ.get(
            "AIODRF_TEST_MONGODB_STANDALONE_URL",
            "mongodb://127.0.0.1:27118/?directConnection=true",
        ),
        "NAME": "aiodrf",
    },
}
DATABASE_ROUTERS = ["tests.ecosystem.mongodb.models.StandaloneRouter"]

INSTALLED_APPS = [
    "rest_framework",
    "django_filters",
    "aiodrf",
    "aiodrf.contrib.mongodb",
    "tests.ecosystem.mongodb",
]

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# No django.contrib.auth: its models need migrations written for ObjectIdAutoField.
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "UNAUTHENTICATED_USER": None,
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
}

# ``AIODRF_TEST_PROFILE=tuned``: the benchmark's tuned profile (tests/profiles.py).
from tests.profiles import apply as _apply_profile  # noqa: E402

_apply_profile(globals())
