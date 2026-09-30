"""Api contracts for the policies example."""

import pytest
from django.contrib.auth.models import User
from django.test import Client
from rest_framework.authtoken.models import Token

from aiodrf.utils import run_sync

pytestmark = pytest.mark.django_db(transaction=True)


async def test_auth_challenge_and_token(client):
    response = await client.get("/private/")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Token"
    user = await User.objects.acreate_user(username="reader", password="local-only")
    token = await Token.objects.acreate(user=user)
    result = await client.get(
        "/private/", headers={"Authorization": f"Token {token.key}"}
    )
    assert result.json() == {"username": "reader"}


async def test_conditional_query_and_public_cache(client):
    assert (await client.get("/public/")).json() == {"public": True}
    assert (
        await client.get("/conditional/", headers={"If-None-Match": '"demo-v1"'})
    ).status_code == 304
    result = await client.request("QUERY", "/search/", json={"term": "Ada"})
    assert result.json() == {"matches": ["Ada"]}
    assert (await client.request("QUERY", "/search/", json={})).status_code == 400


async def test_session_authentication_retains_csrf_enforcement(client):
    user = await User.objects.acreate_user(
        username="session-reader", password="local-only"
    )
    login = Client(enforce_csrf_checks=True)
    await run_sync(login.force_login)(user)
    for key, value in login.cookies.items():
        client.cookies.set(key, value.value)
    assert (await client.get("/private/")).json() == {"username": "session-reader"}
    assert (await client.post("/private/", json={})).status_code == 403


async def test_query_post_fallback_and_openapi(client):
    query = await client.request("QUERY", "/search/", json={"term": "Ada"})
    post = await client.post("/search/", json={"term": "Ada"})
    assert query.status_code == post.status_code == 200
    assert query.json() == post.json()
    response = await client.get("/schema/", headers={"Accept": "application/json"})
    assert response.status_code == 200
    schema = response.json()
    from drf_spectacular.validation import validate_schema

    validate_schema(schema)
    operation = schema["paths"]["/search/"]
    assert "post" in operation
    assert "query" not in operation
    assert operation["post"]["requestBody"]["content"]["application/json"]
    assert (await client.get("/docs/")).status_code == 200
