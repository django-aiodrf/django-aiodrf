"""Verify middleware profiles in separate interpreter processes."""

import uuid

import pytest
from channels.testing import WebsocketCommunicator
from demo.models import Record
from django.conf import settings
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import Client
from project.asgi import application

from aiodrf.utils import run_sync

pytestmark = pytest.mark.django_db(transaction=True)


async def test_http_health_static_and_management_command(client):
    response = await client.get("/ping/")
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert (await client.get("/health/?format=json")).status_code == 200


def test_vendor_typed_command_warning_is_explicit():
    from demo.management.commands.count_records import Command

    # django-typer 4.1 passes a deprecated Typer completion argument. Assert the
    # known vendor warning here; do not suppress warnings for other commands.
    with pytest.warns(
        DeprecationWarning, match="In Typer, only the parameter 'autocompletion'"
    ):
        command = Command()
    assert call_command(command) == "Records: 0"


def test_whitenoise_static_files_on_its_synchronous_serving_path():
    if not any(
        middleware in settings.MIDDLEWARE
        for middleware in (
            "whitenoise.middleware.WhiteNoiseMiddleware",
            "aiodrf.contrib.whitenoise.whitenoise_middleware",
        )
    ):
        pytest.skip("The default profile exercises ServeStatic through ASGI")
    # WhiteNoise uses a synchronous file iterator. Test that contract with WSGI;
    # ASGI deployments should serve these files through their reverse proxy.
    response = Client().get("/static/demo/status.txt")
    try:
        assert response.status_code == 200
        assert (
            b"".join(response.streaming_content) == b"aiodrf local platform example\n"
        )
    finally:
        response.close()


async def test_asgi_static_files_use_the_vendor_async_path(client):
    if "servestatic.middleware.ServeStaticMiddleware" not in settings.MIDDLEWARE:
        pytest.skip("The WhiteNoise comparison has a synchronous static iterator")
    response = await client.get("/static/demo/status.txt")
    assert response.status_code == 200
    assert response.content == b"aiodrf local platform example\n"
    assert (await client.head("/static/demo/status.txt")).content == b""
    conditional = await client.get(
        "/static/demo/status.txt", headers={"If-None-Match": response.headers["etag"]}
    )
    assert conditional.status_code == 304
    assert (await client.get("/static/missing.txt")).status_code == 404


async def test_whitenoise_adapter_retains_sync_iterator_warning(client):
    if "aiodrf.contrib.whitenoise.whitenoise_middleware" not in settings.MIDDLEWARE:
        pytest.skip("Select the dual-mode WhiteNoise adapter profile")
    with pytest.warns(Warning, match="must consume synchronous iterators"):
        response = await client.get("/static/demo/status.txt")
    assert response.status_code == 200
    assert response.content == b"aiodrf local platform example\n"


async def test_crud_and_optional_profile_contracts(client):
    headers = {"Idempotency-Key": str(uuid.uuid4()), "X-Request-ID": "example-request"}
    response = await client.post(
        "/records/", json={"title": "Example"}, headers=headers
    )
    assert response.status_code == 201, response.text
    assert (await client.get("/records/")).json()[0]["title"] == "Example"
    if "idempotency_key.middleware.IdempotencyKeyMiddleware" in settings.MIDDLEWARE:
        replay = await client.post(
            "/records/", json={"title": "Example"}, headers=headers
        )
        assert replay.status_code == 409
        assert await Record.objects.acount() == 1
        assert (
            await client.post("/records/", json={"title": "No key"})
        ).status_code == 400
    if "log_request_id.middleware.RequestIDMiddleware" in settings.MIDDLEWARE:
        response = await client.get("/ping/", headers=headers)
        assert response.headers["X-Request-ID"] == "example-request"
        assert response.json()["request_id"] == "example-request"
    if "django_prometheus" in settings.INSTALLED_APPS:
        response = await client.get("/metrics")
        assert response.status_code == 200
        assert "django_http_requests" in response.text
    if "silk" in settings.INSTALLED_APPS:
        from silk.models import Request

        assert await Request.objects.filter(path="/records/").aexists()
    if "wireup.integration.django" in settings.INSTALLED_APPS:
        assert (await client.get("/injected/")).json() == {"greeting": "Hello, reader"}
    if "cachalot" in settings.INSTALLED_APPS:
        # The update must invalidate a previously cached list.
        await Record.objects.filter(pk=response.json()["id"]).aupdate(title="Changed")
        assert (await client.get("/records/")).json()[0]["title"] == "Changed"


async def test_websocket_permission_and_serialization():
    websocket = WebsocketCommunicator(
        application, "/ws/records/", headers=[(b"origin", b"http://testserver")]
    )
    connected, _ = await websocket.connect()
    assert not connected
    await websocket.disconnect()
    await Record.objects.acreate(title="public")
    websocket = WebsocketCommunicator(
        application, "/ws/records/", headers=[(b"origin", b"http://testserver")]
    )
    try:
        connected, _ = await websocket.connect()
        assert connected
        await websocket.send_json_to({"action": "list", "request_id": 1})
        response = await websocket.receive_json_from()
        assert response["response_status"] == 200
        assert response["data"][0]["title"] == "public"
    finally:
        await websocket.disconnect()


async def test_axes_uses_the_vendor_authentication_signals(client):
    if "axes" not in settings.INSTALLED_APPS:
        pytest.skip("Run with project.settings_protection for django-axes")
    await run_sync(User.objects.create_user)(
        "reader", password="local-example-password"
    )
    client.auth = ("reader", "local-example-password")
    assert (await client.get("/identity/")).status_code == 200
    client.auth = ("reader", "incorrect")
    assert (await client.get("/identity/")).status_code != 200
    assert (await client.get("/identity/")).status_code != 200
    client.auth = ("reader", "local-example-password")
    assert (await client.get("/identity/")).status_code != 200


async def test_zeal_context_covers_async_request_workers(client):
    if "zeal" not in settings.INSTALLED_APPS:
        pytest.skip("Run with project.settings_cache for django-zeal")
    from zeal import zeal_context

    await Record.objects.acreate(title="Loaded")
    # Context variables follow Django's worker boundary. This flat serializer
    # has no lazy relation; the repository suite also proves N+1 detection on
    # an intentionally unoptimized nested serializer.
    with zeal_context():
        response = await client.get("/records/")
    assert response.status_code == 200
    assert response.json()[0]["title"] == "Loaded"
