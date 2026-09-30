"""OpenSearch document registry without automatic network activity."""

from tests.settings import *  # noqa: F403

INSTALLED_APPS = [*INSTALLED_APPS, "django_opensearch_dsl"]  # noqa: F405
OPENSEARCH_DSL = {"default": {"hosts": "http://localhost:9200"}}
OPENSEARCH_DSL_AUTOSYNC = False
