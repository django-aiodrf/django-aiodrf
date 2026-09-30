"""Opt-in grouping must change scheduling, not Django's middleware hooks."""

import asyncio
import threading
from contextvars import ContextVar

import pytest
from asgiref.sync import SyncToAsync
from django.contrib import messages
from django.contrib.auth.models import User
from django.core.asgi import get_asgi_application
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse, JsonResponse
from django.middleware.csrf import get_token
from django.test import Client, override_settings
from django.urls import path
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated

from aiodrf import checks
from aiodrf.response import EventStreamResponse, Response
from aiodrf.settings import ASGIREF_VERSION, aiodrf_settings
from aiodrf.test import AsyncAPIClient
from aiodrf.unsafe import middleware
from aiodrf.utils import run_sync
from aiodrf.views import APIView
from tests.asgi_driver import ASGIDriver, http_scope

CLASSES = (
    middleware.SecurityMiddleware,
    middleware.SessionMiddleware,
    middleware.CommonMiddleware,
    middleware.CsrfViewMiddleware,
    middleware.AuthenticationMiddleware,
    middleware.MessageMiddleware,
    middleware.XFrameOptionsMiddleware,
)
PATHS = [f"{cls.__module__}.{cls.__name__}" for cls in CLASSES]
marker = ContextVar("grouped_middleware_test_marker", default=None)


@pytest.fixture(params=[False, True], ids=["default", "grouped"])
def stack(request):
    if request.param and ASGIREF_VERSION < (3, 12, 1):
        pytest.skip(
            "Grouping deliberately requires the verified asgiref 3.12.1 baseline"
        )
    with override_settings(
        AIODRF={"UNSAFE_SYNC_MIDDLEWARE": request.param},
        MIDDLEWARE=PATHS,
        ROOT_URLCONF=(),
        SESSION_ENGINE="django.contrib.sessions.backends.signed_cookies",
    ):
        yield request.param


@pytest.mark.parametrize("cls", CLASSES)
def test_capability_is_opt_in_and_hook_implementations_are_unchanged(cls):
    django_class = cls.__bases__[1]
    with override_settings(AIODRF={}):
        assert aiodrf_settings.UNSAFE_SYNC_MIDDLEWARE is False
        assert cls.async_capable is True
    with override_settings(AIODRF={"UNSAFE_SYNC_MIDDLEWARE": True}):
        if ASGIREF_VERSION < (3, 12, 1):
            with pytest.raises(ImproperlyConfigured, match=r"asgiref>=3\.12\.1"):
                getattr(cls, "async_capable")  # noqa: B009 -- exercise the descriptor
            return
        assert cls.async_capable is False
        assert cls.sync_capable is True
        assert django_class.async_capable is True
        for name in (
            "__init__",
            "__call__",
            "__acall__",
            "process_request",
            "process_view",
            "process_response",
            "process_exception",
        ):
            assert getattr(cls, name, None) is getattr(django_class, name, None)
    assert cls.async_capable is True


@pytest.mark.parametrize("value", [1, "false", None])
def test_invalid_opt_in_is_reported_without_crashing_deployment_checks(value):
    with override_settings(
        AIODRF={"UNSAFE_SYNC_MIDDLEWARE": value},
        MIDDLEWARE=PATHS,
        ASGI_APPLICATION="project.asgi.application",
    ):
        assert [item.id for item in checks.check_settings(None)] == ["aiodrf.E006"]
        assert checks.check_asgi_deployment(None) == []
        with pytest.raises(ImproperlyConfigured, match="UNSAFE_SYNC_MIDDLEWARE"):
            get_asgi_application()


def test_unsupported_adapters_are_rejected_before_constructing_the_handler(monkeypatch):
    monkeypatch.setattr("aiodrf.settings.ASGIREF_VERSION", (3, 8, 1))
    with override_settings(
        AIODRF={"UNSAFE_SYNC_MIDDLEWARE": True},
        MIDDLEWARE=PATHS,
        ASGI_APPLICATION="project.asgi.application",
    ):
        assert [item.id for item in checks.check_settings(None)] == ["aiodrf.E006"]
        assert checks.check_asgi_deployment(None) == []
        with pytest.raises(ImproperlyConfigured, match=r"asgiref>=3\.12\.1"):
            get_asgi_application()


def test_deployment_check_exposes_the_thread_tradeoff(stack):
    with override_settings(ASGI_APPLICATION="project.asgi.application"):
        warnings = checks.check_asgi_deployment(None)
    assert [item.id for item in warnings] == (
        ["aiodrf.W005"] * len(CLASSES) if stack else []
    )


class Public(APIView):
    authentication_classes = []
    permission_classes = []

    async def get(self, request):
        return Response({"ok": True})


async def test_redirects_and_security_headers(stack):
    with override_settings(
        ROOT_URLCONF=(path("ok/", Public.as_view()),),
        SECURE_SSL_REDIRECT=True,
        SECURE_HSTS_SECONDS=100,
        APPEND_SLASH=True,
    ):
        client = AsyncAPIClient()
        redirect = await client.get("/ok/")
        assert redirect.status_code == 301
        assert redirect["Location"] == "https://testserver/ok/"
        redirect = await client.get("/ok", secure=True)
        assert redirect.status_code == 301
        assert redirect["Location"] == "/ok/"
        response = await client.get("/ok/", secure=True)
        assert response.status_code == 200
        assert response["Strict-Transport-Security"] == "max-age=100"
        assert response["X-Content-Type-Options"] == "nosniff"
        assert response["X-Frame-Options"] == "DENY"


async def test_messages_session_cookie_and_vary_survive_grouping(stack):
    class View(Public):
        async def post(self, request):
            await run_sync(messages.info)(request._request, "saved")
            return Response({"ok": True})

        async def get(self, request):
            values = await run_sync(
                lambda: list(map(str, messages.get_messages(request)))
            )()
            return Response(values)

    with override_settings(
        ROOT_URLCONF=(path("", View.as_view()),),
        MESSAGE_STORAGE="django.contrib.messages.storage.session.SessionStorage",
    ):
        client = AsyncAPIClient()
        response = await client.post("/", {}, format="json")
        assert "sessionid" in response.cookies
        assert "Cookie" in response["Vary"]
        assert (await client.get("/")).data == ["saved"]
        assert (await client.get("/")).data == []


@pytest.mark.django_db(transaction=True)
async def test_database_session_authentication_and_csrf_remain_enforced(
    stack, worker_connections
):
    class Account(APIView):
        authentication_classes = [SessionAuthentication]
        permission_classes = [IsAuthenticated]

        async def get(self, request):
            return Response({"token": get_token(request._request)})

        async def post(self, request):
            return Response({"user": (await request.auser()).username})

    user = await User.objects.acreate(username="grouped")
    with override_settings(
        ROOT_URLCONF=(path("", Account.as_view()),),
        SESSION_ENGINE="django.contrib.sessions.backends.db",
    ):
        client = AsyncAPIClient(enforce_csrf_checks=True)
        assert (await client.get("/")).status_code == 403
        await client.aforce_login(user)
        token = (await client.get("/", secure=True)).data["token"]
        assert (await client.post("/", {}, secure=True)).status_code == 403
        headers = {"x-csrftoken": token, "origin": "https://untrusted.example"}
        assert (
            await client.post("/", {}, secure=True, headers=headers)
        ).status_code == 403
        headers["origin"] = "https://testserver"
        response = await client.post("/", {}, secure=True, headers=headers)
        assert response.status_code == 200
        assert response.data == {"user": "grouped"}


@pytest.mark.parametrize("async_view", [False, True])
def test_wsgi_and_sync_views_keep_working(stack, async_view):
    def view(request):
        return JsonResponse({"ok": True})

    with override_settings(
        ROOT_URLCONF=(path("", Public.as_view() if async_view else view),)
    ):
        response = Client().get("/")
        assert response.json() == {"ok": True}
        assert response["X-Frame-Options"] == "DENY"


class ProbeSecurityMiddleware(middleware.SecurityMiddleware):
    def process_request(self, request):
        marker.set(request.path)
        request.scope["state"]["trace"].append(
            ("before", threading.get_ident(), marker.get())
        )
        return super().process_request(request)

    def process_response(self, request, response):
        request.scope["state"]["trace"].append(
            ("after", threading.get_ident(), marker.get())
        )
        return super().process_response(request, response)


async def test_concurrent_requests_keep_context_and_thread_affinity(stack):
    loop_thread = threading.get_ident()

    async def view(request, pk):
        assert threading.get_ident() == loop_thread
        assert marker.get() == request.path
        trace = request.scope["state"]["trace"]
        await run_sync(
            lambda: trace.append(("view", threading.get_ident(), marker.get()))
        )()
        await asyncio.sleep(0)
        assert marker.get() == request.path
        return HttpResponse("ok")

    with override_settings(
        ROOT_URLCONF=(path("<int:pk>/", view),),
        MIDDLEWARE=[f"{__name__}.ProbeSecurityMiddleware", *PATHS[1:]],
    ):
        application = get_asgi_application()

        async def request(index):
            trace = []
            scope = http_scope(f"/{index}/", state={"trace": trace})
            async with ASGIDriver(application, scope) as driver:
                await driver.incoming.put({"type": "http.request", "body": b""})
                assert (await driver.receive())["status"] == 200
                await driver.finish()
            assert [item[0] for item in trace] == ["before", "view", "after"]
            assert len({item[1] for item in trace}) == 1
            assert trace[0][1] != loop_thread
            assert {item[2] for item in trace} == {f"/{index}/"}

        await asyncio.gather(*(request(index) for index in range(8)))
    assert marker.get() is None


async def test_django_csrf_process_view_is_not_bypassed(stack):
    async def view(request):
        return JsonResponse({"token": get_token(request)})

    with override_settings(ROOT_URLCONF=(path("", view),)):
        client = AsyncAPIClient(enforce_csrf_checks=True)
        token = (await client.get("/")).json()["token"]
        assert (await client.post("/", {})).status_code == 403
        response = await client.post("/", {}, headers={"x-csrftoken": token})
        assert response.status_code == 200


class RecoveringCommonMiddleware(middleware.CommonMiddleware):
    def process_exception(self, request, exception):
        # Custom exception hooks, including database work, still belong off-loop.
        with pytest.raises(RuntimeError, match="no running event loop"):
            asyncio.get_running_loop()
        assert isinstance(exception, ValueError)
        return HttpResponse("handled", status=418)


async def test_custom_exception_hook_runs_in_a_worker_and_preserves_response_hooks(
    stack,
):
    async def view(request):
        raise ValueError("view failed")

    paths = [
        f"{__name__}.RecoveringCommonMiddleware"
        if path.endswith(".CommonMiddleware")
        else path
        for path in PATHS
    ]
    with override_settings(ROOT_URLCONF=(path("", view),), MIDDLEWARE=paths):
        response = await AsyncAPIClient().get("/")
        assert response.status_code == 418
        assert response.content == b"handled"
        assert response["X-Frame-Options"] == "DENY"


@pytest.mark.parametrize("stream", [False, True])
async def test_disconnect_cancels_view_or_stream_without_deadlock(stack, stream):
    entered, closed = asyncio.Event(), asyncio.Event()

    async def wait():
        try:
            entered.set()
            await asyncio.Event().wait()
        finally:
            closed.set()

    class View(Public):
        async def get(self, request):
            if not stream:
                await wait()

            async def events():
                yield {"ready": True}
                await wait()

            return EventStreamResponse(events())

    with override_settings(ROOT_URLCONF=(path("", View.as_view()),)):
        async with ASGIDriver(get_asgi_application(), http_scope()) as driver:
            await driver.incoming.put({"type": "http.request", "body": b""})
            await asyncio.wait_for(entered.wait(), 3)
            await driver.incoming.put({"type": "http.disconnect"})
            await driver.finish()
            assert closed.is_set()


async def test_grouping_reduces_crossings_without_changing_the_response(monkeypatch):
    if ASGIREF_VERSION < (3, 12, 1):
        pytest.skip(
            "Grouping deliberately requires the verified asgiref 3.12.1 baseline"
        )
    calls = []
    original = SyncToAsync.__call__

    async def traced(self, *args, **kwargs):
        calls.append(self.func.__qualname__)
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(SyncToAsync, "__call__", traced)
    counts = {}
    responses = []
    for enabled in (False, True):
        with override_settings(
            AIODRF={"UNSAFE_SYNC_MIDDLEWARE": enabled},
            MIDDLEWARE=PATHS,
            ROOT_URLCONF=(path("", Public.as_view()),),
        ):
            application = get_asgi_application()
            calls.clear()
            async with ASGIDriver(application, http_scope()) as driver:
                await driver.incoming.put({"type": "http.request", "body": b""})
                assert (await driver.receive())["status"] == 200
                await driver.finish()
                responses.append(driver.sent)
            counts[enabled] = len(calls)
            if enabled:
                assert "SessionMiddleware.process_response" not in calls
                assert "CsrfViewMiddleware.process_view" in calls
    assert responses[0] == responses[1]
    assert counts[True] < counts[False]
    print("SyncToAsync crossings (default, grouped):", counts)
