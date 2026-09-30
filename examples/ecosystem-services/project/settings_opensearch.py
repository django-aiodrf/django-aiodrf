"""OpenSearch model mapping with explicit publication after database writes."""

from .settings import *  # noqa: F403

if not EXAMPLE_OPENSEARCH_URL:  # noqa: F405
    raise ValueError("Set EXAMPLE_OPENSEARCH_URL before selecting OpenSearch")
INSTALLED_APPS = [*INSTALLED_APPS, "django_opensearch_dsl"]  # noqa: F405
OPENSEARCH_DSL = {"default": {"hosts": EXAMPLE_OPENSEARCH_URL}}  # noqa: F405
OPENSEARCH_DSL_AUTOSYNC = False
