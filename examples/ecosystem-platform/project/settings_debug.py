"""Local HTML diagnostics; never expose the toolbar on a public deployment."""

from .settings import *  # noqa: F403

DEBUG = True
INTERNAL_IPS = ["127.0.0.1"]
INSTALLED_APPS = [*INSTALLED_APPS, "debug_toolbar"]  # noqa: F405
MIDDLEWARE = [*MIDDLEWARE, "debug_toolbar.middleware.DebugToolbarMiddleware"]  # noqa: F405
