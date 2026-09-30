"""
django-structlog binds the request's id and user into structlog's context
variables. aiodrf runs synchronous hooks in a worker thread and async handlers
on the loop; log lines from both have to carry the context of their request.
"""

import asyncio
import gc
from contextlib import contextmanager

import pytest
import structlog
from asgiref.sync import markcoroutinefunction, sync_to_async
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import include, path
from django.utils.functional import SimpleLazyObject
from rest_framework import generics as drf_generics
from rest_framework import serializers
from rest_framework import views as drf_views
from rest_framework.authentication import SessionAuthentication
from rest_framework.response import Response as DRFResponse
from rest_framework.routers import SimpleRouter
from structlog.testing import LogCapture

from aiodrf import generics, viewsets
from aiodrf.response import Response
from aiodrf.test import AsyncAPIClient, count_hops
from aiodrf.views import APIView
from tests.base import both_transports
from tests.ecosystem.base import UserFixture
from tests.testapp.models import Author

logger = structlog.get_logger("tests.ecosystem.structlog")


class AuthorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Author
        fields = ["id", "name"]


class Logging:
    """Synchronous hooks: aiodrf runs them in its worker thread."""

    queryset = Author.objects.all()
    serializer_class = AuthorSerializer

    def get_queryset(self):
        logger.info("get_queryset", tag=self.request.query_params.get("tag"))
        return super().get_queryset()

    def perform_create(self, serializer):
        logger.info("perform_create")
        serializer.save()


class DRFAuthors(Logging, drf_generics.ListCreateAPIView):
    pass


class Authors(Logging, viewsets.ModelViewSet):
    pass


class DRFHello(drf_views.APIView):
    def get(self, request):
        logger.info("handler")
        return DRFResponse({})


class Hello(APIView):
    async def get(self, request):
        logger.info("handler")  # on the event loop
        return Response({})


class DRFBroken(drf_views.APIView):
    def get(self, request):
        raise RuntimeError("broken")


class Broken(APIView):
    async def get(self, request):
        raise RuntimeError("broken")


class Plain(generics.ListAPIView):
    queryset = Author.objects.all()
    serializer_class = AuthorSerializer


class WhoAmI(APIView):
    authentication_classes = [SessionAuthentication]

    async def get(self, request):
        return Response({"user": request.user.username})


class TokenUserMiddleware:
    # A token middleware before django-structlog's: its user is what
    # structlog wraps again.
    async_capable = True
    sync_capable = False

    def __init__(self, get_response):
        self.get_response = get_response
        markcoroutinefunction(self)

    async def __call__(self, request):
        request.user = SimpleLazyObject(
            lambda: User(username="token-user", is_active=True)
        )
        return await self.get_response(request)


router = SimpleRouter()
router.register("authors", Authors)
urlpatterns = [
    path("drf/authors/", DRFAuthors.as_view()),
    path("aiodrf/", include(router.urls)),
    path("drf/hello/", DRFHello.as_view()),
    path("aiodrf/hello/", Hello.as_view()),
    path("drf/broken/", DRFBroken.as_view()),
    path("aiodrf/broken/", Broken.as_view()),
    path("plain/", Plain.as_view()),
    path("whoami/", WhoAmI.as_view()),
]
structlog_middleware = override_settings(
    ROOT_URLCONF=__name__,
    MIDDLEWARE=[
        "django.contrib.sessions.middleware.SessionMiddleware",
        "django.contrib.auth.middleware.AuthenticationMiddleware",
        "django_structlog.middlewares.RequestMiddleware",
    ],
)


@pytest.fixture(autouse=True, scope="module")
def release_middleware():
    yield
    # RequestMiddleware connects ``got_request_exception`` weakly when it is
    # built, once per client handler here, and those handlers are cyclic
    # garbage: until collected, each would log other modules' request
    # exceptions, with the renderer reading tracebacks' locals (a queryset
    # queries, in the thread Django sends the signal from).
    gc.collect()


@contextmanager
def captured_logs():
    """structlog's ``LogCapture`` after the context variables are merged."""
    previous = structlog.get_config()
    capture = LogCapture()
    structlog.configure(processors=[structlog.contextvars.merge_contextvars, capture])
    try:
        yield capture.entries
    finally:
        structlog.configure(**previous)


def context_of(entries, *events):
    return [
        (entry["event"], entry.get("request_id"), entry.get("user_id"))
        for entry in entries
        if entry["event"] in events
    ]


@both_transports
class _StructlogTests(UserFixture):
    @structlog_middleware
    async def test_worker_thread_hooks_log_with_the_request_context(self):
        await sync_to_async(self.client.force_login)(self.user)
        pk = self.user.pk
        for prefix in ("drf", "aiodrf"):
            with self.subTest(prefix=prefix), captured_logs() as entries:
                created = await self.api(
                    "post",
                    f"/{prefix}/authors/",
                    data={"name": "Ada"},
                    HTTP_X_REQUEST_ID=f"{prefix}-create",
                )
                listed = await self.api(
                    "get", f"/{prefix}/authors/", HTTP_X_REQUEST_ID=f"{prefix}-list"
                )
                assert (created.status_code, listed.status_code) == (201, 200)
                assert context_of(
                    entries, "perform_create", "get_queryset", "request_finished"
                ) == [
                    ("perform_create", f"{prefix}-create", pk),
                    ("request_finished", f"{prefix}-create", pk),
                    ("get_queryset", f"{prefix}-list", pk),
                    ("request_finished", f"{prefix}-list", pk),
                ]

    @structlog_middleware
    async def test_async_handlers_log_with_the_request_context(self):
        for prefix in ("drf", "aiodrf"):
            with self.subTest(prefix=prefix), captured_logs() as entries:
                response = await self.api(
                    "get", f"/{prefix}/hello/", HTTP_X_REQUEST_ID=f"{prefix}-hello"
                )
                assert response.status_code == 200
                assert context_of(
                    entries, "request_started", "handler", "request_finished"
                ) == [
                    ("request_started", f"{prefix}-hello", None),
                    ("handler", f"{prefix}-hello", None),
                    ("request_finished", f"{prefix}-hello", None),
                ]

    @structlog_middleware
    async def test_an_unhandled_exception_is_logged_as_request_failed(self):
        self.client.raise_request_exception = False
        for prefix in ("drf", "aiodrf"):
            with self.subTest(prefix=prefix), captured_logs() as entries:
                response = await self.api(
                    "get", f"/{prefix}/broken/", HTTP_X_REQUEST_ID=f"{prefix}-broken"
                )
                assert response.status_code == 500
                failed = [
                    entry for entry in entries if entry["event"] == "request_failed"
                ]
                assert [(entry["request_id"], entry["code"]) for entry in failed] == [
                    (f"{prefix}-broken", 500)
                ]
                assert "request_finished" not in [entry["event"] for entry in entries]


@structlog_middleware
class ConcurrencyTests(TestCase):
    async def test_concurrent_requests_keep_their_own_context_in_the_worker(self):
        client = AsyncAPIClient()
        tags = [str(number) for number in range(8)]
        with captured_logs() as entries:
            responses = await asyncio.gather(
                *(
                    client.get(
                        f"/aiodrf/authors/?tag={tag}", HTTP_X_REQUEST_ID=f"id-{tag}"
                    )
                    for tag in tags
                )
            )
        assert {response.status_code for response in responses} == {200}
        logged = sorted(
            (entry["tag"], entry["request_id"])
            for entry in entries
            if entry["event"] == "get_queryset"
        )
        assert logged == [(tag, f"id-{tag}") for tag in tags]

    @override_settings(
        MIDDLEWARE=[
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
            f"{__name__}.TokenUserMiddleware",
            "django_structlog.middlewares.RequestMiddleware",
        ]
    )
    async def test_the_user_it_wraps_again_is_kept_without_a_hop(self):
        with captured_logs(), count_hops() as hops:
            response = await AsyncAPIClient().get("/whoami/")
        assert response.data == {"user": "token-user"}
        assert hops.calls == []

    async def test_the_middleware_adds_no_aiodrf_hop(self):
        # Its own ``sync_to_async`` calls (prepare, handle_response) are
        # asgiref's, around the view; the list still costs aiodrf one hop.
        with captured_logs(), count_hops() as hops:
            response = await AsyncAPIClient().get("/plain/")
        assert response.status_code == 200
        assert hops.count == 1, hops.calls
