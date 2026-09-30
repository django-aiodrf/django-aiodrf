"""Enable site-wide native page caching for the public demonstration API."""

from django.core.exceptions import ImproperlyConfigured

from .settings import *  # noqa: F403 -- Django settings composition
from .settings import EXAMPLE_CACHE_URL, MIDDLEWARE

if not EXAMPLE_CACHE_URL:
    raise ImproperlyConfigured(
        "Set the URL for EXAMPLE_CACHE_BACKEND before enabling page caching."
    )

MIDDLEWARE = ["demo.middleware.UpdateCache", *MIDDLEWARE, "demo.middleware.FetchCache"]
CACHE_MIDDLEWARE_SECONDS = 30
CACHE_MIDDLEWARE_KEY_PREFIX = "public-example-pages"
