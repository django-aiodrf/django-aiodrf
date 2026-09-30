"""Object-level authorization and real vendor login tokens through ASGI."""

from datetime import timedelta

import pytest
from auditlog.models import LogEntry
from demo.models import Article, Note
from django.contrib.auth.models import User
from django.utils import timezone
from guardian.shortcuts import assign_perm
from oauth2_provider.models import AccessToken, Application

from aiodrf.utils import run_sync

pytestmark = pytest.mark.django_db(transaction=True)


def user_and_permissions():
    user = User.objects.create_user("reader", password="local-test-password")
    for permission in (
        "view_article",
        "add_article",
        "change_article",
        "delete_article",
    ):
        assign_perm("demo." + permission, user)
    return user


async def test_guardian_filters_lists_and_checks_detail_mutations(client):
    user = await run_sync(user_and_permissions)()
    visible = await Article.objects.acreate(owner=user, title="Visible")
    hidden = await Article.objects.acreate(owner=user, title="Hidden")
    await run_sync(assign_perm)("view_article", user, visible)
    client.auth = ("reader", "local-test-password")
    response = await client.get("/articles/?title=Visible")
    assert [row["id"] for row in response.json()] == [visible.pk]
    assert (await client.get(f"/articles/{hidden.pk}/")).status_code == 404
    assert (
        await client.patch(f"/articles/{visible.pk}/", json={"title": "Denied"})
    ).status_code == 403
    assert (await Article.objects.aget(pk=visible.pk)).title == "Visible"
    created = await client.post(
        "/articles/", json={"title": "Created", "sensitive_note": "private"}
    )
    assert created.status_code == 201
    assert "sensitive_note" not in created.json()
    entry = await LogEntry.objects.filter(
        object_pk=str(created.json()["id"]), actor=user
    ).afirst()
    assert entry is not None
    assert "private" not in str(entry.changes)


async def test_rules_scope_list_and_detail_to_the_owner(client):
    await run_sync(user_and_permissions)()
    other = await User.objects.acreate(username="other")
    foreign = await Note.objects.acreate(owner=other, text="Private")
    client.auth = ("reader", "local-test-password")
    created = await client.post("/notes/", json={"text": "Owned"})
    assert created.status_code == 201
    assert [row["text"] for row in (await client.get("/notes/")).json()] == ["Owned"]
    assert (await client.get(f"/notes/{foreign.pk}/")).status_code == 404


async def test_djoser_and_allauth_credentials_authenticate_real_requests(client):
    await run_sync(user_and_permissions)()
    credentials = {"username": "reader", "password": "local-test-password"}
    login = await client.post("/djoser/token/login/", json=credentials)
    assert login.status_code == 200
    response = await client.get(
        "/identity/token/",
        headers={"Authorization": "Token " + login.json()["auth_token"]},
    )
    assert response.json() == {"username": "reader"}
    login = await client.post("/_allauth/app/v1/auth/login", json=credentials)
    assert login.status_code == 200
    response = await client.get(
        "/identity/allauth/",
        headers={"X-Session-Token": login.json()["meta"]["session_token"]},
    )
    assert response.json() == {"username": "reader"}


async def test_anonymous_and_preflight_contracts(client):
    assert (await client.get("/identity/oauth/")).status_code == 401
    assert (await client.get("/identity/allauth/")).status_code == 403
    response = await client.options(
        "/articles/",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


async def test_oauth_scope_and_expiry_are_enforced(client):
    user = await run_sync(user_and_permissions)()
    app = await Application.objects.acreate(
        user=user,
        name="Local example",
        client_type=Application.CLIENT_CONFIDENTIAL,
        authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
    )
    token = await AccessToken.objects.acreate(
        user=user,
        application=app,
        token="example-read-token",
        expires=timezone.now() + timedelta(minutes=5),
        scope="read",
    )
    headers = {"Authorization": "Bearer example-read-token"}
    assert (await client.get("/identity/oauth/", headers=headers)).json() == {
        "username": "reader"
    }
    token.scope = "write"
    await token.asave(update_fields=["scope"])
    assert (await client.get("/identity/oauth/", headers=headers)).status_code == 403
    token.expires = timezone.now() - timedelta(seconds=1)
    await token.asave(update_fields=["expires"])
    assert (await client.get("/identity/oauth/", headers=headers)).status_code == 401


async def test_dj_rest_auth_cookie_authenticates(client):
    await run_sync(user_and_permissions)()
    response = await client.post(
        "/dj-rest-auth/login/",
        json={"username": "reader", "password": "local-test-password"},
    )
    assert response.status_code == 200
    assert "example-auth" in client.cookies
    assert (await client.get("/identity/cookie/")).json() == {"username": "reader"}
