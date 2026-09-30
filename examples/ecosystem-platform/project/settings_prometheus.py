"""Prometheus middleware surrounds Django's normal request stack."""

from .settings import *  # noqa: F403
from .settings import MIDDLEWARE as BASE_MIDDLEWARE

INSTALLED_APPS = [*INSTALLED_APPS, "django_prometheus"]  # noqa: F405
MIDDLEWARE = [
    "django_prometheus.middleware.PrometheusBeforeMiddleware",
    *BASE_MIDDLEWARE,
    "django_prometheus.middleware.PrometheusAfterMiddleware",
]
