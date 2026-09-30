"""Explicit search-index mapping for durable job records."""

from django.apps import apps
from django.conf import settings
from django_elasticsearch_dsl import Document
from django_elasticsearch_dsl.registries import registry

from .models import Job


@registry.register_document
class JobDocument(Document):
    class Index:
        name = settings.EXAMPLE_SEARCH_INDEX
        settings = {"number_of_shards": 1, "number_of_replicas": 0}

    class Django:
        model = Job
        fields = ["title", "completed"]

    def prepare(self, instance):
        # django-elasticsearch-dsl 9.0 and elasticsearch-py 9.4+ disagree on
        # _prepared_fields storage. Use the vendor's preparation hooks without
        # changing Document or the registry process-wide.
        return {name: prepare(instance) for name, _, prepare in self.init_prepare()}


if apps.is_installed("django_opensearch_dsl"):
    # Vendor autodiscovery imports this module; registration lives separately.
    from .opensearch_documents import JobOpenSearchDocument  # noqa: F401
