"""Direct Elasticsearch document mapping, independent of Django model signals."""

from elasticsearch.dsl import AsyncDocument, Boolean, Text


class SearchItem(AsyncDocument):
    title = Text()
    completed = Boolean()
