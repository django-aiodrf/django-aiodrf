"""Local development settings; not a deployment configuration."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = "local-example-only-do-not-deploy"  # noqa: S105
DEBUG = False
ALLOWED_HOSTS = os.environ.get(
    "EXAMPLE_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver"
).split(",")
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]
ROOT_URLCONF = "project.urls"
USE_TZ = True
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "UNAUTHENTICATED_USER": None,
}
MAILERS = {"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}}
AIODRF = {"LIFESPAN": "demo.lifecycle.lifespan"}
INSTALLED_APPS = ["rest_framework", "aiodrf", "aiodrf.contrib.mongodb", "demo"]
DEFAULT_AUTO_FIELD = "django_mongodb_backend.fields.ObjectIdAutoField"
DATABASES = {
    "default": {
        "ENGINE": "django_mongodb_backend",
        "HOST": os.environ.get(
            "MONGODB_URL", "mongodb://127.0.0.1:27017/?replicaSet=rs0"
        ),
        "NAME": os.environ.get("MONGODB_DATABASE", "aiodrf_example_mongodb"),
    }
}
