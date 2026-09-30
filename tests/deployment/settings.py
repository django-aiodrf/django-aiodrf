"""Normal Django settings, loaded independently by each test-owned process."""

import json
import os
from pathlib import Path

CONFIG = json.loads(Path(os.environ["AIODRF_DEPLOYMENT_CONFIG"]).read_text())
SECRET_KEY = CONFIG["secret"]
DEBUG = False
ALLOWED_HOSTS = ["127.0.0.1", "localhost", "testserver"]
ROOT_URLCONF = "tests.deployment.urls"
ASGI_APPLICATION = "tests.deployment.asgi.application"
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "rest_framework",
    "aiodrf",
    "tests.testapp",
]
if CONFIG.get("db_backend") == "async":
    INSTALLED_APPS.append("django_async_backend")
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
if CONFIG.get("middleware_mode", "standard") == "sync":
    AIODRF = {"UNSAFE_SYNC_MIDDLEWARE": True}
    MIDDLEWARE = [
        "aiodrf.unsafe.middleware." + path.rsplit(".", 1)[1] for path in MIDDLEWARE
    ]
elif CONFIG.get("middleware_mode") == "inline":
    MIDDLEWARE = [
        "tests.deployment.inline_middleware." + path.rsplit(".", 1)[1]
        if path.rsplit(".", 1)[1]
        in ("SecurityMiddleware", "CommonMiddleware", "XFrameOptionsMiddleware")
        else path
        for path in MIDDLEWARE
    ]
DATABASES = {"default": CONFIG["database"]}
DEFAULT_AUTO_FIELD = "django.db.models.AutoField"
USE_TZ = True
SESSION_ENGINE = "django.contrib.sessions.backends.db"
FILE_UPLOAD_MAX_MEMORY_SIZE = 65536
DATA_UPLOAD_MAX_MEMORY_SIZE = 16 * 1024 * 1024
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication"
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
}
