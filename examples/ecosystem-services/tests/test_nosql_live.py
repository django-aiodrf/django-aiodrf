"""Optional service contracts; use dedicated example indexes and cache databases."""

import os
from uuid import uuid4

import pytest
from django.conf import settings
from elasticsearch import AsyncElasticsearch

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.skipif(
    not os.environ.get("EXAMPLE_SEARCH_URL"),
    reason="Set EXAMPLE_SEARCH_URL for live search",
)
async def test_django_and_native_search_reads_and_writes(client):
    index = settings.EXAMPLE_SEARCH_INDEX
    if not index.startswith("aiodrf-example-test-"):
        pytest.fail("Use an EXAMPLE_SEARCH_INDEX starting with aiodrf-example-test-")
    async with AsyncElasticsearch(settings.EXAMPLE_SEARCH_URL) as search:
        # Never delete an index that existed before this test.
        await search.indices.create(
            index=index,
            mappings={
                "properties": {
                    "title": {"type": "text"},
                    "completed": {"type": "boolean"},
                }
            },
        )
        try:
            job = await client.post("/jobs/", json={"title": "Search fixture"})
            assert job.status_code == 201
            published = await client.post(
                "/search/django/", json={"job_id": job.json()["id"]}
            )
            assert published.status_code == 200, published.text
            native = await client.post("/search/", json={"title": "Search native"})
            assert native.status_code == 201, native.text
            for url in ("/search/?q=Search", "/search/django/?q=Search"):
                response = await client.get(url)
                assert response.status_code == 200, response.text
                assert sorted(response.json()["titles"]) == [
                    "Search fixture",
                    "Search native",
                ]
        finally:
            await search.indices.delete(index=index)


@pytest.mark.skipif(
    not os.environ.get("EXAMPLE_VALKEY_URL"),
    reason="Set EXAMPLE_VALKEY_URL for live cache",
)
async def test_native_cache_round_trip(client):
    title = "Cache fixture " + uuid4().hex
    response = await client.post("/cache/", json={"title": title})
    assert response.status_code == 200, response.text
    response = await client.get("/cache/")
    assert response.json() == {"value": {"title": title, "completed": False}}
