"""
django-elasticsearch-dsl against a real Elasticsearch: authors indexed
through aiodrf's views, then found with the synchronous search of a
``get_queryset`` and with ``AsyncSearch`` in an ``async def`` handler.

``nox -s ecosystem_elasticsearch`` sets ``AIODRF_TEST_ELASTICSEARCH_URL``
(and installs ``elasticsearch[async]``); without it these tests are skipped.
"""

import os
import unittest

from django.test import TestCase, override_settings
from elasticsearch.dsl import async_connections

from aiodrf.test import AsyncAPIClient
from tests.ecosystem.documents import AuthorDocument
from tests.testapp.models import Author

URL = os.environ.get("AIODRF_TEST_ELASTICSEARCH_URL")


@unittest.skipUnless(URL, "AIODRF_TEST_ELASTICSEARCH_URL is not set")
@override_settings(
    ROOT_URLCONF="tests.ecosystem.test_elasticsearch", ELASTICSEARCH_DSL_AUTOSYNC=True
)
class LiveElasticsearchTests(TestCase):
    def setUp(self):
        super().setUp()
        index = AuthorDocument._index
        index.delete(ignore_unavailable=True)
        index.create()
        self.addCleanup(index.delete, ignore_unavailable=True)

    async def index_authors(self, client):
        for name in ("Ursula Le Guin", "Octavia Butler", "Ursula Vernon"):
            response = await client.post("/authors/", {"name": name})
            assert response.status_code == 201, response.data
        deleted = await Author.objects.aget(name="Ursula Vernon")
        assert (await client.delete(f"/authors/{deleted.pk}/")).status_code == 204
        renamed = await Author.objects.aget(name="Octavia Butler")
        response = await client.patch(
            f"/authors/{renamed.pk}/", {"name": "Octavia E. Butler"}
        )
        assert response.status_code == 200, response.data

    async def test_indexed_through_the_views_and_found_in_get_queryset(self):
        client = AsyncAPIClient()
        await self.index_authors(client)
        # ``refresh=true`` on each ``_bulk``: searchable at once.
        response = await client.get("/search/", {"q": "ursula"})
        assert [author["name"] for author in response.data] == ["Ursula Le Guin"]
        response = await client.get("/search/", {"q": "butler"})
        assert [author["name"] for author in response.data] == ["Octavia E. Butler"]

    async def test_async_search_in_an_async_handler(self):
        client = AsyncAPIClient()
        await self.index_authors(client)
        # The aiohttp session of the async client belongs to this test's loop.
        async_connections.configure(default={"hosts": URL})
        try:
            response = await client.get("/async-search/", {"q": "ursula octavia"})
        finally:
            await async_connections.get_connection().close()
            async_connections.configure()
        assert sorted(response.data) == ["Octavia E. Butler", "Ursula Le Guin"]
