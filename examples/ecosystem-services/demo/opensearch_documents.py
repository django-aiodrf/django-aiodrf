"""OpenSearch model mapping independent of Elasticsearch's document registry."""

from django.conf import settings
from django_opensearch_dsl import Document
from django_opensearch_dsl.registries import registry

from .models import Job


@registry.register_document
class JobOpenSearchDocument(Document):
    class Index:
        name = settings.EXAMPLE_OPENSEARCH_INDEX
        settings = {"number_of_shards": 1, "number_of_replicas": 0}

    class Django:
        model = Job
        fields = ["title", "completed"]
        ignore_signals = True
