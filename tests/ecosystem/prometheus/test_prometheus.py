"""
django-prometheus counts aiodrf views like any Django view: by status, view
name and method, through Django's ASGI handler.

What it measures is the middleware's: the latency ends when the response
starts, so a streamed body is not part of it, and a method outside its list
(QUERY) is labelled ``<invalid method>`` in the response counters but
``QUERY`` in the latency histogram. Labels are the view's name, never a user
or a full URL.
"""

import asyncio

from django.test import TestCase
from django.urls import path
from prometheus_client import REGISTRY
from rest_framework.permissions import AllowAny

from aiodrf.response import Response, StreamingResponse
from aiodrf.test import AsyncAPIClient
from aiodrf.views import APIView

BODY_DELAY = 0.3


class Open(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]


class Hello(Open):
    async def get(self, request):
        return Response({"hello": "world"})

    async def query(self, request):
        return Response({"query": True})


class Broken(Open):
    async def get(self, request):
        raise RuntimeError("unexpected")


class Slow(Open):
    async def get(self, request):
        async def items():
            yield {"n": 1}
            await asyncio.sleep(BODY_DELAY)
            yield {"n": 2}

        return StreamingResponse(items())


urlpatterns = [
    path("hello/", Hello.as_view(), name="hello"),
    path("broken/", Broken.as_view(), name="broken"),
    path("slow/", Slow.as_view(), name="slow"),
]


def sample(name, **labels):
    return REGISTRY.get_sample_value(name, labels) or 0.0


RESPONSES = "django_http_responses_total_by_status_view_method_total"
LATENCY = "django_http_requests_latency_seconds_by_view_method"


class PrometheusTests(TestCase):
    async def test_responses_are_counted_by_status_view_and_method(self):
        labels = {"status": "200", "view": "hello", "method": "GET"}
        before = sample(RESPONSES, **labels)
        client = AsyncAPIClient()
        for _ in range(3):
            assert (await client.get("/hello/")).status_code == 200
        assert sample(RESPONSES, **labels) == before + 3
        family = next(
            f for f in REGISTRY.collect() if f.name == RESPONSES.removesuffix("_total")
        )
        assert {key for s in family.samples for key in s.labels} <= {
            "status",
            "view",
            "method",
        }

    async def test_an_exception_past_drfs_handler_is_counted(self):
        before = sample(
            "django_http_exceptions_total_by_type_total", type="RuntimeError"
        )
        client = AsyncAPIClient(raise_request_exception=False)
        assert (await client.get("/broken/")).status_code == 500
        after = sample(
            "django_http_exceptions_total_by_type_total", type="RuntimeError"
        )
        assert after == before + 1

    async def test_the_latency_ends_when_a_streamed_body_starts(self):
        before = sample(f"{LATENCY}_sum", view="slow", method="GET")
        response = await AsyncAPIClient().get("/slow/")
        assert (
            b"".join([chunk async for chunk in response.streaming_content]).count(b"\n")
            == 2
        )
        observed = sample(f"{LATENCY}_sum", view="slow", method="GET") - before
        assert 0 < observed < BODY_DELAY

    async def test_query_is_an_invalid_method_to_the_counters(self):
        before = sample(
            RESPONSES, status="200", view="hello", method="<invalid method>"
        )
        response = await AsyncAPIClient().generic(
            "QUERY", "/hello/", b"{}", content_type="application/json"
        )
        assert response.status_code == 200
        assert sample(
            RESPONSES, status="200", view="hello", method="<invalid method>"
        ) == (before + 1)
        assert sample(f"{LATENCY}_count", view="hello", method="QUERY") >= 1
