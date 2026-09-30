"""Local contracts do not contact S3, Elasticsearch or an error-tracking service."""

import pytest
from demo.models import Job
from django.core.files.storage import default_storage

from aiodrf.utils import run_sync

pytestmark = pytest.mark.django_db(transaction=True)


async def test_committed_work_request_executes_the_eager_task(client):
    response = await client.post("/jobs/", json={"title": "Example"})
    assert response.status_code == 201, response.text
    stored = await Job.objects.aget(pk=response.json()["id"])
    assert stored.completed


async def test_storage_uses_the_configured_backend(client):
    response = await client.post("/storage/", json={"text": "Example"})
    assert response.status_code == 201
    name = response.json()["name"]

    def read_and_delete():
        try:
            with default_storage.open(name) as stored:
                return stored.read()
        finally:
            default_storage.delete(name)

    assert await run_sync(read_and_delete)() == b"Example"
    invalid = await client.post("/storage/", json={"text": "x" * 4097})
    assert invalid.status_code == 400


@pytest.mark.parametrize(
    "url", ["/search/", "/search/django/", "/opensearch/", "/cache/"]
)
@pytest.mark.parametrize("method", ["get", "post"])
async def test_unconfigured_services_are_explicit(client, url, method):
    response = await getattr(client, method)(url)
    assert response.status_code == 503


async def test_native_search_write_awaits_dsl_save(client, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from demo import views
    from django.conf import settings

    transport = object()
    monkeypatch.setattr(
        views, "get_lifespan_state", lambda *args: SimpleNamespace(search=transport)
    )
    save = AsyncMock()
    monkeypatch.setattr(views.SearchItem, "save", save)
    response = await client.post(
        "/search/", json={"title": "Indexed", "completed": False}
    )
    assert response.status_code == 201
    assert len(response.json()["id"]) == 32
    save.assert_awaited_once_with(
        using=transport, index=settings.EXAMPLE_SEARCH_INDEX, refresh="wait_for"
    )
    invalid = await client.post("/search/", json={"title": ""})
    assert invalid.status_code == 400
    assert save.await_count == 1


async def test_native_cache_view_awaits_backend(client, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from demo import views

    value = {"title": "Cached", "completed": False}
    backend = SimpleNamespace(aset=AsyncMock(), aget=AsyncMock(return_value=value))
    monkeypatch.setattr(
        views, "get_lifespan_state", lambda *args: SimpleNamespace(cache=backend)
    )
    response = await client.post("/cache/", json=value)
    assert response.status_code == 200
    backend.aset.assert_awaited_once_with("value", value, timeout=30)
    response = await client.get("/cache/")
    assert response.json() == {"value": value}
    backend.aget.assert_awaited_once_with("value")


async def test_opensearch_uses_its_own_client_and_document_mapping(client, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from demo import views

    transport = SimpleNamespace(
        index=AsyncMock(),
        search=AsyncMock(
            return_value={"hits": {"hits": [{"_source": {"title": "OpenSearch job"}}]}}
        ),
    )
    monkeypatch.setattr(
        views, "get_lifespan_state", lambda *args: SimpleNamespace(opensearch=transport)
    )
    job = await Job.objects.acreate(title="OpenSearch job")
    response = await client.post("/opensearch/", json={"job_id": job.pk})
    assert response.status_code == 201, response.text
    assert transport.index.call_args.kwargs["body"] == {
        "title": "OpenSearch job",
        "completed": False,
    }
    assert (await client.get("/opensearch/?q=job")).json() == {
        "titles": ["OpenSearch job"]
    }
    assert (await client.post("/opensearch/", json={"job_id": -1})).status_code == 400


async def test_cache_page_response_varies_by_tenant(client):
    response = await client.get("/cache-page/", headers={"X-Tenant": "one"})
    assert response.status_code == 200
    assert response.json()["tenant"] == "one"
    assert "X-Tenant" in response.headers["vary"]
