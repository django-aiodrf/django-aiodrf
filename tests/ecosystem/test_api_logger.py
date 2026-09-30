"""drf-api-logger: its async-capable middleware logs aiodrf views as it logs DRF's."""

import threading

import pytest
from django.test import TestCase, override_settings
from django.urls import path
from drf_api_logger import API_LOGGER_SIGNAL
from rest_framework import views as drf_views
from rest_framework.permissions import AllowAny
from rest_framework.response import Response as DRFResponse

from aiodrf.response import Response
from aiodrf.test import AsyncAPIClient, count_hops
from aiodrf.views import APIView
from tests.base import both_transports
from tests.settings import MIDDLEWARE


class DRFEcho(drf_views.APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        return DRFResponse({"echo": request.data, "password": "hunter2"}, status=201)


class Echo(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    async def post(self, request):
        return Response(
            {"echo": await request.adata(), "password": "hunter2"}, status=201
        )


urlpatterns = [path("drf/", DRFEcho.as_view()), path("aiodrf/", Echo.as_view())]
logged = override_settings(
    ROOT_URLCONF=__name__,
    MIDDLEWARE=[
        *MIDDLEWARE,
        "drf_api_logger.middleware.api_logger_middleware.APILoggerMiddleware",
    ],
    DRF_API_LOGGER_SIGNAL=True,
)
# drf-api-logger 1.4.0 returns from a ``finally`` block, which CPython 3.14
# warns about at compile time (PEP 765); warnings are errors in this suite.
pytestmark = pytest.mark.filterwarnings(
    "ignore:'return' in a 'finally' block:SyntaxWarning"
)
PAYLOAD = {"name": "Ged", "password": "secret", "tags": ["mage"]}


class Listener:
    def setUp(self):
        super().setUp()
        self.logs = []
        API_LOGGER_SIGNAL.listen += self.listen

    def tearDown(self):
        API_LOGGER_SIGNAL.listen -= self.listen
        super().tearDown()

    def listen(self, **data):
        self.logs.append({**data, "thread": threading.current_thread()})


def logged_fields(log):
    return {key: log[key] for key in ("method", "body", "response", "status_code")}


@both_transports
class _LogParityTests(Listener):
    @logged
    async def test_the_log_of_an_aiodrf_view_equals_the_one_of_the_drf_view(self):
        drf = await self.api("post", "/drf/", data=PAYLOAD)
        aiodrf = await self.api("post", "/aiodrf/", data=PAYLOAD)
        assert drf.status_code == aiodrf.status_code == 201
        # The middleware reads the body before the view; the view still parses it.
        assert aiodrf.data["echo"] == PAYLOAD
        drf_log, aiodrf_log = self.logs
        assert logged_fields(drf_log) == logged_fields(aiodrf_log)
        assert aiodrf_log["api"].endswith("/aiodrf/")
        # Its masking applies to both the request and the response body.
        assert (
            aiodrf_log["body"]["password"]
            == aiodrf_log["response"]["password"]
            == "***FILTERED***"
        )


@logged
class LoopTests(Listener, TestCase):
    async def test_under_asgi_the_log_is_emitted_on_the_event_loop_without_a_hop(self):
        # A listener doing blocking I/O would block the loop, and one using
        # the ORM would raise SynchronousOnlyOperation: listeners have to hand
        # the work off (drf-api-logger's own database mode queues it for its
        # thread).
        with count_hops() as hops:
            response = await AsyncAPIClient().post("/aiodrf/", data=PAYLOAD)
        assert response.status_code == 201
        assert hops.calls == []
        (log,) = self.logs
        assert log["thread"] is threading.current_thread()
