"""Opt-in Redis query caching; use a dedicated database for this application."""

from .settings import *  # noqa: F403

INSTALLED_APPS = [*INSTALLED_APPS, "cacheops"]  # noqa: F405
CACHEOPS_REDIS = os.environ.get("EXAMPLE_CACHEOPS_URL", "redis://127.0.0.1:6380/14")  # noqa: F405
CACHEOPS = {"demo.record": {"ops": "all", "timeout": 60}}
