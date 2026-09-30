"""Django service behavior through the actual ASGI application."""

import os
from uuid import uuid4

import pytest
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.test import override_settings
from project.settings import CACHE_BACKENDS


@pytest.mark.django_db(transaction=True)
async def test_model_defaults_generated_output_and_commit_callback(client):
    response = await client.post("/entries/", json={"title": "Example"})
    assert response.status_code == 201
    data = response.json()
    assert data["amount"] == 1
    assert data["total"] == 2
    assert await cache.aget("last-entry") == data["id"]
    assert (await client.get("/template/")).status_code == 200


@pytest.mark.parametrize("backend", ["locmem", "dummy", "file", "database"])
@pytest.mark.django_db(transaction=True)
async def test_builtin_cache_backends(client, backend, tmp_path):
    from aiodrf.utils import run_sync

    config = dict(CACHE_BACKENDS[backend])
    if backend == "file":
        config["LOCATION"] = str(tmp_path / "cache")
    with override_settings(CACHES={"default": config}):
        if backend == "database":
            await run_sync(call_command)("createcachetable", verbosity=0)
        response = await client.post("/cache/", json={"value": "stored"})
        assert response.status_code == 200
        result = await client.get("/cache/")
        assert result.json() == {"value": None if backend == "dummy" else "stored"}
        await cache.adelete("sample")


async def test_forms_mail_and_async_signal(client):
    response = await client.post(
        "/contact/", json={"email": "invalid", "message": "Example"}
    )
    assert response.status_code == 400
    response = await client.post(
        "/contact/", json={"email": "reader@example.org", "message": "Example"}
    )
    assert response.status_code == 202
    assert len(mail.outbox) == 1
    response = await client.post("/signals/", json={"value": "example"})
    assert response.json() == {"results": ["EXAMPLE"]}


@pytest.mark.django_db(transaction=True)
async def test_authentication_views_and_anonymous_session_access(client):
    assert (await client.get("/accounts/login/")).status_code == 200
    assert (await client.get("/admin/")).status_code == 302
    assert (await client.get("/session/")).status_code == 403


@pytest.mark.django_db(transaction=True)
async def test_feeds_and_sitemaps_share_public_model_data(client):
    entry = await client.post("/entries/", json={"title": "Public entry"})
    assert entry.status_code == 201
    assert "Public entry" in (await client.get("/feed/")).text
    sitemap = await client.get("/sitemap.xml")
    assert sitemap.status_code == 200
    assert f"/entries/{entry.json()['id']}/" in sitemap.text


@pytest.mark.parametrize("backend", ["redis", "django-redis", "pymemcache", "pylibmc"])
async def test_explicit_external_cache_backend(backend):
    if backend not in os.environ.get("EXAMPLE_TEST_CACHE_BACKENDS", "").split(","):
        pytest.skip("Select this backend with EXAMPLE_TEST_CACHE_BACKENDS")
    config = {**CACHE_BACKENDS[backend], "KEY_PREFIX": "aiodrf-example-test"}
    key = "contract-" + uuid4().hex
    with override_settings(CACHES={"default": config}):
        try:
            await cache.aset(key, "stored", timeout=30)
            assert await cache.aget(key) == "stored"
        finally:
            await cache.adelete(key)
            await cache.aclose()
