"""Django Elasticsearch documents are indexed explicitly, not inside model signals."""

from .settings import *  # noqa: F403

if not EXAMPLE_SEARCH_URL:  # noqa: F405
    raise ValueError("Set EXAMPLE_SEARCH_URL before using project.settings_search")
INSTALLED_APPS = [*INSTALLED_APPS, "django_elasticsearch_dsl"]  # noqa: F405
ELASTICSEARCH_DSL = {"default": {"hosts": EXAMPLE_SEARCH_URL}}  # noqa: F405
ELASTICSEARCH_DSL_AUTOSYNC = False
