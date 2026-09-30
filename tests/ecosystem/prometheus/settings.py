"""
django-prometheus, in a project of its own: its middleware wraps every other
one, and 2.5.0 (the latest release) declares Django < 6.1
(``nox -s ecosystem_prometheus``, on Django 6.0).
"""

from tests.settings import *  # noqa: F403

INSTALLED_APPS = [*INSTALLED_APPS, "django_prometheus"]  # noqa: F405
MIDDLEWARE = [
    "django_prometheus.middleware.PrometheusBeforeMiddleware",
    *MIDDLEWARE,  # noqa: F405
    "django_prometheus.middleware.PrometheusAfterMiddleware",
]
ROOT_URLCONF = "tests.ecosystem.prometheus.test_prometheus"
