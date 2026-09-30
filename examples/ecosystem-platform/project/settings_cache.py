"""Local query caching and N+1 detection; these packages own their ORM instrumentation."""

from .settings import *  # noqa: F403

INSTALLED_APPS = [*INSTALLED_APPS, "cachalot", "zeal"]  # noqa: F405
CACHALOT_ENABLED = True
