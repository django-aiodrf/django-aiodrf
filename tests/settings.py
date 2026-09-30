import django

SECRET_KEY = "aiodrf-tests"
DEBUG = False
USE_TZ = True
TIME_ZONE = "UTC"
ROOT_URLCONF = "tests.urls"
DEFAULT_AUTO_FIELD = "django.db.models.AutoField"

DATABASES = {
    "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"},
    # Only the router tests (tests/test_django_edges.py) write here.
    "other": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"},
}

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "rest_framework",
    "rest_framework.authtoken",
    "django_filters",
    "drf_spectacular",
    "aiodrf",
    "tests.testapp",
]

MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": ["django.template.context_processors.request"]
        },
    }
]

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# Django 6.0 Tasks, or the optional Django 5 backport. No runtime aliasing.
TASK_MODULE = "django.tasks" if django.VERSION >= (6, 0) else "django_tasks"
TASKS = {
    "default": {"BACKEND": f"{TASK_MODULE}.backends.immediate.ImmediateBackend"},
    "queued": {"BACKEND": f"{TASK_MODULE}.backends.dummy.DummyBackend"},
}

MEDIA_URL = "/media/"

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
    "LIST_SERIALIZER_ERRORS_AS_DICT": True,
    "DEFAULT_THROTTLE_RATES": {"user": "3/min", "anon": "3/min"},
}

# Django 6.1 deprecates the implicit default mailer; older versions ignore this.
MAILERS = {"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}}

# ``AIODRF_TEST_PROFILE=tuned``: the benchmark's tuned profile (tests/profiles.py).
from tests.profiles import apply as _apply_profile  # noqa: E402

_apply_profile(globals())
