"""
django-log-request-id keeps the request id in an ``asgiref.local.Local``
that its logging filter reads. Its middleware is synchronous; aiodrf's hooks
log from the worker thread and async handlers from the loop, and both have to
see the id of their own request.
"""

import asyncio
import logging
from contextlib import contextmanager

from django.test import TestCase, override_settings
from django.urls import include, path
from log_request_id.filters import RequestIDFilter
from rest_framework import generics as drf_generics
from rest_framework import serializers
from rest_framework import views as drf_views
from rest_framework.response import Response as DRFResponse
from rest_framework.routers import SimpleRouter

from aiodrf import viewsets
from aiodrf.response import Response
from aiodrf.test import AsyncAPIClient, count_hops
from aiodrf.views import APIView
from tests.base import both_transports
from tests.testapp.models import Author

logger = logging.getLogger("tests.ecosystem.log_request_id")


class AuthorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Author
        fields = ["id", "name"]


class Logging:
    """Synchronous hooks: aiodrf runs them in its worker thread."""

    authentication_classes = []
    permission_classes = []
    queryset = Author.objects.all()
    serializer_class = AuthorSerializer

    def get_queryset(self):
        logger.info("get_queryset %s", self.request.query_params.get("tag"))
        return super().get_queryset()

    def perform_create(self, serializer):
        logger.info("perform_create")
        serializer.save()


class DRFAuthors(Logging, drf_generics.ListCreateAPIView):
    pass


class Authors(Logging, viewsets.ModelViewSet):
    pass


class DRFHello(drf_views.APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        logger.info("handler")
        return DRFResponse({"id": request.id})


class Hello(APIView):
    authentication_classes = []
    permission_classes = []

    async def get(self, request):
        logger.info("handler")  # on the event loop
        return Response({"id": request.id})


router = SimpleRouter()
router.register("authors", Authors)
urlpatterns = [
    path("drf/authors/", DRFAuthors.as_view()),
    path("aiodrf/", include(router.urls)),
    path("drf/hello/", DRFHello.as_view()),
    path("aiodrf/hello/", Hello.as_view()),
]
request_id_middleware = override_settings(
    ROOT_URLCONF=__name__,
    MIDDLEWARE=["log_request_id.middleware.RequestIDMiddleware"],
    LOG_REQUEST_ID_HEADER="HTTP_X_REQUEST_ID",
    REQUEST_ID_RESPONSE_HEADER="X-Request-ID",
)


class Records(logging.Handler):
    def __init__(self):
        super().__init__()
        self.addFilter(RequestIDFilter())
        self.records = []

    def emit(self, record):
        self.records.append((record.getMessage(), record.request_id))


@contextmanager
def captured_records():
    handler = Records()
    logger.addHandler(handler)
    level = logger.level
    logger.setLevel(logging.INFO)
    try:
        yield handler.records
    finally:
        logger.setLevel(level)
        logger.removeHandler(handler)


@both_transports
class _RequestIdTests:
    @request_id_middleware
    async def test_worker_thread_hooks_log_the_request_id(self):
        for prefix in ("drf", "aiodrf"):
            with self.subTest(prefix=prefix), captured_records() as records:
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
                assert listed.headers["X-Request-ID"] == f"{prefix}-list"
                assert records == [
                    ("perform_create", f"{prefix}-create"),
                    ("get_queryset None", f"{prefix}-list"),
                ]

    @request_id_middleware
    async def test_async_handlers_log_the_request_id(self):
        for prefix in ("drf", "aiodrf"):
            with self.subTest(prefix=prefix), captured_records() as records:
                response = await self.api(
                    "get", f"/{prefix}/hello/", HTTP_X_REQUEST_ID=f"{prefix}-hello"
                )
                assert response.data == {"id": f"{prefix}-hello"}
                assert records == [("handler", f"{prefix}-hello")]


@request_id_middleware
class ConcurrencyTests(TestCase):
    async def test_concurrent_requests_keep_their_own_id_in_the_worker(self):
        client = AsyncAPIClient()
        tags = [str(number) for number in range(8)]
        with captured_records() as records:
            responses = await asyncio.gather(
                *(
                    client.get(
                        f"/aiodrf/authors/?tag={tag}", HTTP_X_REQUEST_ID=f"id-{tag}"
                    )
                    for tag in tags
                )
            )
        assert {response.status_code for response in responses} == {200}
        assert sorted(records) == [(f"get_queryset {tag}", f"id-{tag}") for tag in tags]

    async def test_the_middleware_adds_no_aiodrf_hop(self):
        # Django runs the synchronous middleware in a thread of its own, around
        # the view; the list still costs aiodrf one hop.
        with count_hops() as hops:
            response = await AsyncAPIClient().get("/aiodrf/authors/")
        assert response.status_code == 200
        assert hops.count == 1, hops.calls
