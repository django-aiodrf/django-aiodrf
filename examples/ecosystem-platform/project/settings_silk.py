"""Explicit local SQL profiling using Silk's own instrumentation."""

from .settings import *  # noqa: F403

INSTALLED_APPS = [*INSTALLED_APPS, "silk"]  # noqa: F405
MIDDLEWARE = [*MIDDLEWARE, "silk.middleware.SilkyMiddleware"]  # noqa: F405
SILKY_PYTHON_PROFILER = False
SILKY_AUTHENTICATION = True
SILKY_AUTHORISATION = True
