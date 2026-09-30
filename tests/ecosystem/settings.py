"""
Settings for ``nox -s ecosystem``: the test project plus third-party packages.

Only what needs tables or import-time registration is installed here. What
changes the behaviour of every request (middleware, authentication backends,
exception handlers, cachalot's ORM patch, axes' lockout) is switched on by
the test module of that package with ``override_settings``.
"""

import os

from tests.settings import *  # noqa: F403

INSTALLED_APPS = [
    *INSTALLED_APPS,  # noqa: F405
    "rest_framework_simplejwt",
    "knox",
    "oauth2_provider",
    "dj_rest_auth",
    "guardian",
    "drf_standardized_errors",
    "corsheaders",
    "simple_history",
    "axes",
    "cachalot",
    "zeal",
    "aiodrf.contrib.simplejwt",
    "aiodrf.contrib.knox",
    "tests.ecosystem",
]

# PyJWT warns about HMAC keys shorter than 32 bytes, and warnings are errors.
SECRET_KEY = "aiodrf-ecosystem-tests-0123456789abcdef"

# dj-rest-auth reads its settings once, at import: ``override_settings`` is too late.
REST_AUTH = {"USE_JWT": True, "JWT_AUTH_COOKIE": "auth", "JWT_AUTH_HTTPONLY": False}

AXES_ENABLED = False
CACHALOT_ENABLED = False

# Model fields: django-money, django-phonenumber-field, django-taggit.
INSTALLED_APPS += ["djmoney", "phonenumber_field", "taggit"]

# django-allauth (headless) checks at startup that its middleware is installed;
# it is sync-and-async capable. drf-api-logger stores its log in a model.
INSTALLED_APPS += ["allauth", "allauth.account", "allauth.headless", "drf_api_logger"]
MIDDLEWARE = [*MIDDLEWARE, "allauth.account.middleware.AccountMiddleware"]  # noqa: F405

# django-polymorphic keeps the concrete class in a content type.
INSTALLED_APPS += ["polymorphic"]

# django-auditlog records changes in its own model; its middleware is set by
# test_auditlog.py.
INSTALLED_APPS += ["auditlog"]

# django-silk stores what it records in its models; django-debug-toolbar has
# templates and static files, and looks at the static files storage when it
# starts. Their middleware is switched on per test.
INSTALLED_APPS += ["django.contrib.staticfiles", "silk", "debug_toolbar"]
STATIC_URL = "/static/"

# django-cacheops (test_cacheops.py) caches authors in Redis db 12 of the
# benchmark server (tests/services/compose.yaml); switched on per test, like cachalot.
# It reads CACHEOPS once: the profile cannot be set with ``override_settings``.
INSTALLED_APPS += ["cacheops"]
CACHEOPS_REDIS = (
    f"redis://127.0.0.1:{os.environ.get('AIODRF_BENCH_REDIS_PORT', '6380')}/12"
)
CACHEOPS = {"testapp.author": {"ops": "all", "timeout": 60}}
CACHEOPS_ENABLED = False

# django-cleanup (test_cleanup.py) only for the models marked with ``cleanup.select``.
INSTALLED_APPS += ["django_cleanup.apps.CleanupSelectedConfig"]

# django-elasticsearch-dsl (test_elasticsearch.py) indexes authors when a test
# switches autosync on; test_elasticsearch_live.py needs a server
# (``nox -s ecosystem_elasticsearch``).
INSTALLED_APPS += ["django_elasticsearch_dsl"]
ELASTICSEARCH_DSL = {
    "default": {
        "hosts": os.environ.get(
            "AIODRF_TEST_ELASTICSEARCH_URL", "http://127.0.0.1:9200"
        ),
    }
}
ELASTICSEARCH_DSL_AUTOSYNC = False

# drf-auth-kit (test_auth_kit.py). It reads AUTH_KIT once, at import: the
# cookie profile cannot be set with ``override_settings``.
INSTALLED_APPS += ["auth_kit", "aiodrf.contrib.auth_kit"]
AUTH_KIT = {
    "AUTH_COOKIE_PROFILES": {
        "https://admin.example.com": {
            "AUTH_JWT_COOKIE_NAME": "admin-jwt",
            "AUTH_TOKEN_COOKIE_NAME": "admin-token",
        }
    }
}
