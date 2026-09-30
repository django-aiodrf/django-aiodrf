"""Login lockout and request replay protection retain the vendors' semantics."""

from .settings import *  # noqa: F403
from .settings import MIDDLEWARE as BASE_MIDDLEWARE

INSTALLED_APPS = [*INSTALLED_APPS, "axes"]  # noqa: F405
MIDDLEWARE = [
    *BASE_MIDDLEWARE,
    "axes.middleware.AxesMiddleware",
    "idempotency_key.middleware.IdempotencyKeyMiddleware",
]
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]
AXES_FAILURE_LIMIT = 2
AXES_LOCKOUT_PARAMETERS = ["username", "ip_address"]
