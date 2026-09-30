"""
django-elasticsearch-dsl imports this module when it starts
(``test_elasticsearch.py``, ``test_elasticsearch_live.py``). The index is
kept in sync only where a test sets ``ELASTICSEARCH_DSL_AUTOSYNC``.
"""

from django_elasticsearch_dsl import Document
from django_elasticsearch_dsl.registries import registry

from tests.testapp.models import Author


@registry.register_document
class AuthorDocument(Document):
    class Index:
        name = "aiodrf-authors"
        settings = {"number_of_shards": 1, "number_of_replicas": 0}

    class Django:
        model = Author
        fields = ["name"]

    def prepare(self, instance):
        # django-elasticsearch-dsl 9.0 sets ``self._prepared_fields`` in
        # ``__init__``; elasticsearch-py 9.4+ stores that attribute in the
        # document's data, and ``prepare()`` reads the empty class attribute:
        # every document is indexed as ``{}``. Not an aiodrf matter; DRF too.
        return {name: prepare(instance) for name, _, prepare in self.init_prepare()}
