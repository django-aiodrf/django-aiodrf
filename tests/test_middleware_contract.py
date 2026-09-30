"""Stock Django middleware semantics around async DRF views, without patches."""

import asyncio
import gzip
import zlib

import django
import pytest
from django.contrib import messages
from django.contrib.auth.models import User
from django.core.asgi import get_asgi_application
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.test import override_settings
from django.urls import path
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated

from aiodrf.response import EventStreamResponse, Response
from aiodrf.test import AsyncAPIClient
from aiodrf.utils import run_sync
from aiodrf.views import APIView
from tests.asgi_driver import ASGIDriver, http_scope


class Public(APIView):
    authentication_classes = []
    permission_classes = []

    async def get(self, request):
        return Response({"text": "compressible " * 100})


@pytest.mark.parametrize("compressed", [False, True])
async def test_gzip_conditional_get_and_head_preserve_response_contract(compressed):
    with override_settings(
        ROOT_URLCONF=(path("", Public.as_view()),),
        MIDDLEWARE=[
            "django.middleware.gzip.GZipMiddleware",
            "django.middleware.http.ConditionalGetMiddleware",
            "django.middleware.common.CommonMiddleware",
        ],
    ):
        client = AsyncAPIClient()
        headers = {"accept-encoding": "gzip" if compressed else "identity"}
        response = await client.get("/", headers=headers)
        etag = response["ETag"]
        assert "Accept-Encoding" in response["Vary"]
        assert etag.startswith('W/"') if compressed else etag.startswith('"')
        body = gzip.decompress(response.content) if compressed else response.content
        assert body.startswith(b'{"text":"compressible ')
        assert int(response["Content-Length"]) == len(response.content)
        cached = await client.get("/", headers={**headers, "if-none-match": etag})
        assert cached.status_code == 304
        assert cached.content == b""
        head = await client.head("/", headers=headers)
        assert head.status_code == 200
        assert head.content == b""


async def test_security_common_redirects_and_headers():
    with override_settings(
        ROOT_URLCONF=(path("ok/", Public.as_view()),),
        MIDDLEWARE=[
            "django.middleware.security.SecurityMiddleware",
            "django.middleware.common.CommonMiddleware",
            "django.middleware.clickjacking.XFrameOptionsMiddleware",
        ],
        SECURE_SSL_REDIRECT=True,
        SECURE_HSTS_SECONDS=100,
        APPEND_SLASH=True,
    ):
        client = AsyncAPIClient()
        insecure = await client.get("/ok/")
        assert insecure.status_code == 301
        assert insecure["Location"] == "https://testserver/ok/"
        slash = await client.get("/ok", secure=True)
        assert slash.status_code == 301
        assert slash["Location"] == "/ok/"
        response = await client.get("/ok/", secure=True)
        assert response.status_code == 200
        assert response["Strict-Transport-Security"] == "max-age=100"
        assert response["X-Content-Type-Options"] == "nosniff"
        assert response["X-Frame-Options"] == "DENY"


async def test_messages_and_signed_cookie_session_are_consumed_once():
    class View(Public):
        async def post(self, request):
            await run_sync(messages.info)(request._request, "saved")
            return Response({"ok": True})

        async def get(self, request):
            values = await run_sync(
                lambda: [str(item) for item in messages.get_messages(request._request)]
            )()
            return Response(values)

    with override_settings(
        ROOT_URLCONF=(path("", View.as_view()),),
        MIDDLEWARE=[
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.contrib.messages.middleware.MessageMiddleware",
        ],
        SESSION_ENGINE="django.contrib.sessions.backends.signed_cookies",
        MESSAGE_STORAGE="django.contrib.messages.storage.session.SessionStorage",
    ):
        client = AsyncAPIClient()
        created = await client.post("/", {}, format="json")
        assert "sessionid" in created.cookies
        assert "Cookie" in created["Vary"]
        assert (await client.get("/")).data == ["saved"]
        assert (await client.get("/")).data == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "kind",
    [
        "drf",
        pytest.param(
            "django",
            marks=pytest.mark.xfail(
                django.VERSION < (6, 0),
                strict=True,
                raises=AssertionError,
                reason="Django #36540/#37042: alogin leaves request.auser's anonymous cache stale before 6.0",
            ),
        ),
    ],
)
async def test_remote_user_and_login_required_keep_drf_permission_ownership(
    kind, worker_connections
):
    async def private(request):
        user = await request.auser()
        return JsonResponse({"user": user.username})

    if kind == "drf":

        class Protected(APIView):
            authentication_classes = [SessionAuthentication]
            permission_classes = [IsAuthenticated]

            async def get(self, request):
                return Response({"user": (await request.auser()).username})

        private = Protected.as_view()
    denied_status = 403 if kind == "drf" else 302
    with override_settings(
        ROOT_URLCONF=(path("api/", Public.as_view()), path("private/", private)),
        MIDDLEWARE=[
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
            "django.contrib.auth.middleware.RemoteUserMiddleware",
            "django.contrib.auth.middleware.LoginRequiredMiddleware",
        ],
        AUTHENTICATION_BACKENDS=["django.contrib.auth.backends.RemoteUserBackend"],
    ):
        client = AsyncAPIClient()
        # DRF deliberately opts out; its own permission classes govern APIs.
        assert (await client.get("/api/")).status_code == 200
        denied = await client.get("/private/")
        assert denied.status_code == denied_status
        if kind == "django":
            assert "next=/private/" in denied["Location"]
        response = await client.get("/private/", headers={"remote-user": "alice"})
        assert response.status_code == 200
        assert response.json() == {"user": "alice"}
        # Removing the trusted upstream header must remove remote authentication.
        assert (await client.get("/private/")).status_code == denied_status


@pytest.mark.django_db(transaction=True)
async def test_session_auth_csrf_origin_and_referer_checks_remain_enforced(
    worker_connections,
):
    class View(APIView):
        authentication_classes = [SessionAuthentication]
        permission_classes = [IsAuthenticated]

        async def get(self, request):
            return Response({"token": get_token(request._request)})

        async def post(self, request):
            return Response({"ok": True})

    user = await User.objects.acreate(username="alice")
    with override_settings(ROOT_URLCONF=(path("", View.as_view()),)):
        client = AsyncAPIClient(enforce_csrf_checks=True)
        await client.aforce_login(user)
        initial = await client.get("/", secure=True)
        token = initial.data["token"]
        assert "csrftoken" in initial.cookies
        assert "Cookie" in initial["Vary"]
        assert (
            await client.post("/", {}, format="json", secure=True)
        ).status_code == 403
        headers = {"x-csrftoken": token, "origin": "https://untrusted.example"}
        assert (
            await client.post("/", {}, format="json", secure=True, headers=headers)
        ).status_code == 403
        # Without Origin, secure requests require a valid Referer.
        assert (
            await client.post(
                "/", {}, format="json", secure=True, headers={"x-csrftoken": token}
            )
        ).status_code == 403
        for header in ("origin", "referer"):
            response = await client.post(
                "/",
                {},
                format="json",
                secure=True,
                headers={
                    "x-csrftoken": token,
                    header: "https://testserver/"
                    if header == "referer"
                    else "https://testserver",
                },
            )
            assert response.status_code == 200, response.data


class StreamDeliveryTimeout(TimeoutError):
    """Distinguish upstream buffering from startup or disconnect failures."""


@pytest.mark.parametrize(
    ("compressed", "block_body"),
    [
        (False, False),
        (False, True),
        pytest.param(
            True,
            False,
            marks=pytest.mark.xfail(
                django.VERSION[:2] == (6, 0),
                strict=True,
                raises=StreamDeliveryTimeout,
                reason="Django #36293: 6.0 async GZip buffers small SSE chunks until completion",
            ),
        ),
        (True, True),
    ],
)
async def test_gzip_sse_first_event_heartbeat_backpressure_and_disconnect(
    compressed, block_body
):
    opened, closed = [], []

    async def events():
        opened.append(True)
        try:
            yield {"ready": True}
            await asyncio.Event().wait()
        finally:
            closed.append(True)

    class View(Public):
        async def get(self, request):
            return EventStreamResponse(events(), keepalive=0.01)

    with override_settings(
        ROOT_URLCONF=(path("", View.as_view()),),
        MIDDLEWARE=["django.middleware.gzip.GZipMiddleware"],
    ):
        scope = http_scope()
        scope["headers"].append(
            (b"accept-encoding", b"gzip" if compressed else b"identity")
        )
        async with ASGIDriver(
            get_asgi_application(), scope, block_body=block_body
        ) as driver:
            await driver.incoming.put({"type": "http.request", "body": b""})
            start = await driver.receive()
            assert start["status"] == 200
            encoding = {key.lower(): value for key, value in start["headers"]}.get(
                b"content-encoding"
            )
            assert encoding == (b"gzip" if compressed else None)
            try:
                if block_body:
                    await asyncio.wait_for(driver.blocked.wait(), 3)
                    assert len(opened) <= 1
                else:
                    decoded = b""
                    inflater = zlib.decompressobj(31)
                    async with asyncio.timeout(3):
                        while (
                            b":\n\n" not in decoded
                            or b'data: {"ready":true}\n\n' not in decoded
                        ):
                            body = (await driver.receive()).get("body", b"")
                            if compressed:
                                # Django 5.2 emits independent gzip members per chunk.
                                if inflater.eof:
                                    inflater = zlib.decompressobj(31)
                                decoded += inflater.decompress(body)
                            else:
                                decoded += body
                    assert opened == [True]
            except TimeoutError as exc:
                raise StreamDeliveryTimeout(
                    "SSE delivery exceeded its deadline"
                ) from exc
            finally:
                # Even the known upstream buffering failure must close its request.
                await driver.incoming.put({"type": "http.disconnect"})
                await driver.finish()
                assert closed == opened  # An unentered producer owns no resources.
