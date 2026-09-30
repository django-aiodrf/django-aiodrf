"""Run only in a fresh subprocess: real SDKs, in-memory exporters, no telemetry egress."""

import asyncio
import contextlib
import os
import threading

import httpx
import pytest
from django.contrib.auth.models import User
from django.core.asgi import get_asgi_application
from django.core.wsgi import get_wsgi_application
from django.test import override_settings
from django.urls import path
from django.utils.asyncio import async_unsafe
from rest_framework.authentication import BaseAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView as DRFView

from aiodrf.utils import run_sync
from aiodrf.views import APIView
from tests.asgi_driver import ASGIDriver, http_scope


class Authentication(BaseAuthentication):
    def authenticate(self, request):
        pk = int(request.headers["X-Test-User"])
        return User(pk=pk, username=f"user-{pk}"), None


class Policies:
    authentication_classes = [Authentication]
    permission_classes = [IsAuthenticated]


def span(name, user):
    if os.environ["TEST_SDK"] == "sentry":
        import sentry_sdk

        sentry_sdk.set_tag("test.user", str(user.pk))
        return contextlib.nullcontext()
    from opentelemetry import trace

    return trace.get_tracer(__name__).start_as_current_span(
        name, attributes={"test.user": user.pk}
    )


@async_unsafe("SDK work must run in the worker")
def work(user, failure):
    with span("worker", user):
        if failure == "worker":
            raise ValueError(f"worker-{user.pk}")
        return {"user": user.pk, "thread": threading.get_ident()}


class DRFEndpoint(Policies, DRFView):
    def get(self, request, failure):
        with span("view", request.user):
            if failure == "view":
                raise RuntimeError(f"view-{request.user.pk}")
            return Response(work(request.user, failure))


class Endpoint(Policies, APIView):
    async def get(self, request, failure):
        if failure == "cancel":
            return await cancelled(request._request)
        with span("view", request.user):
            await asyncio.sleep(0)  # Interleave request scopes before worker entry.
            if failure == "view":
                raise RuntimeError(f"view-{request.user.pk}")
            return Response(await run_sync(work)(request.user, failure))


async def cancelled(request):
    state = request.scope["state"]
    try:
        state["entered"].set()
        await asyncio.Event().wait()
    finally:
        state["closed"].set()


urlpatterns = [
    path("django/cancel/", cancelled),
    path("drf/<str:failure>/", DRFEndpoint.as_view()),
    path("aiodrf/<str:failure>/", Endpoint.as_view()),
]


@contextlib.contextmanager
def sentry_events():
    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration
    from sentry_sdk.transport import Transport

    events = []

    class MemoryTransport(Transport):
        def capture_envelope(self, envelope):
            events.extend(
                item.payload.json for item in envelope.items if item.type == "event"
            )

    sentry_sdk.init(
        dsn="https://public@example.invalid/1",
        transport=MemoryTransport,
        default_integrations=False,
        integrations=[DjangoIntegration()],
        send_default_pii=True,
        auto_session_tracking=False,
    )
    try:
        yield events
        sentry_sdk.flush()
    finally:
        sentry_sdk.get_client().close()


@contextlib.contextmanager
def otel_spans():
    from opentelemetry import trace
    from opentelemetry.instrumentation.django import DjangoInstrumentor
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    DjangoInstrumentor().instrument(tracer_provider=provider)
    try:
        yield exporter
    finally:
        DjangoInstrumentor().uninstrument()
        provider.shutdown()


def check_events(events):
    import sentry_sdk

    assert len(events) == 4  # Exactly one event for each unhandled exception.
    for event in events:
        user = int(event["user"]["id"])
        assert event["tags"]["test.user"] == str(user)
        exception = event["exception"]["values"][-1]
        assert exception["value"].endswith(f"-{user}")
        framework = "drf" if user < 20 else "aiodrf"
        assert f"/{framework}/" in event["request"]["url"]
    sentry_sdk.capture_message("outside-request")
    outside = events[-1]
    assert outside["message"] == "outside-request"
    assert "test.user" not in outside.get("tags", {})
    assert not outside.get("user")


def check_spans(exporter, count):
    from opentelemetry import trace

    spans = exporter.get_finished_spans()
    servers = [s for s in spans if s.kind is trace.SpanKind.SERVER]
    assert len(servers) == count
    assert len({s.context.trace_id for s in servers}) == count
    for server in servers:
        children = [
            s for s in spans if s.parent and s.parent.span_id == server.context.span_id
        ]
        assert len(children) == 1
        view = children[0]
        user = view.attributes["test.user"]
        workers = [
            s for s in spans if s.parent and s.parent.span_id == view.context.span_id
        ]
        assert len(workers) == (0 if user % 10 == 3 else 1)
        for worker in workers:
            assert worker.attributes["test.user"] == user
            assert worker.context.trace_id == server.context.trace_id
        assert server.attributes["http.status_code"] == (200 if user % 10 == 1 else 500)
        assert "<str:failure>" in server.attributes["http.route"]
        if user % 10 != 1:
            assert server.status.status_code is trace.StatusCode.ERROR
        assert server.end_time is not None
    assert not trace.get_current_span().get_span_context().is_valid


@override_settings(ROOT_URLCONF=__name__, MIDDLEWARE=[], ALLOWED_HOSTS=["testserver"])
async def test_context_and_error_parity():
    sdk = os.environ["TEST_SDK"]

    requests = [
        (framework, failure, index * 10 + number)
        for index, framework in enumerate(("drf", "aiodrf"), 1)
        for number, failure in enumerate(("ok", "worker", "view"), 1)
    ]

    async def request(framework, failure, user):
        url = f"/{framework}/{failure}/"
        if os.environ["TEST_TRANSPORT"] == "asgi":
            transport = httpx.ASGITransport(app=application, raise_app_exceptions=False)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                response = await client.get(url, headers={"X-Test-User": str(user)})
        else:

            def get():
                transport = httpx.WSGITransport(
                    app=wsgi_application, raise_app_exceptions=False
                )
                with httpx.Client(
                    transport=transport, base_url="http://testserver"
                ) as client:
                    return client.get(url, headers={"X-Test-User": str(user)})

            response = await run_sync(get)()
        assert response.status_code == (200 if failure == "ok" else 500)
        if failure == "ok":
            assert response.json()["user"] == user
            assert response.json()["thread"] != threading.get_ident()

    with sentry_events() if sdk == "sentry" else otel_spans() as observed:
        application = get_asgi_application()
        wsgi_application = get_wsgi_application()
        await asyncio.gather(*(request(*item) for item in requests))
        if sdk == "sentry":
            check_events(observed)
        else:
            check_spans(observed, len(requests))


@override_settings(ROOT_URLCONF=__name__, MIDDLEWARE=[], ALLOWED_HOSTS=["testserver"])
async def test_disconnect():
    sdk = os.environ["TEST_SDK"]
    with sentry_events() if sdk == "sentry" else otel_spans() as observed:
        application = get_asgi_application()
        completed_spans = []
        for framework in ("django", "aiodrf"):
            state = {"entered": asyncio.Event(), "closed": asyncio.Event()}
            scope = http_scope(f"/{framework}/cancel/", state=state)
            scope["headers"].append((b"x-test-user", b"1"))
            async with ASGIDriver(application, scope) as driver:
                await driver.incoming.put({"type": "http.request", "body": b""})
                await asyncio.wait_for(state["entered"].wait(), 3)
                await driver.incoming.put({"type": "http.disconnect"})
                await driver.finish()
                assert state["closed"].is_set()
            if sdk == "otel":
                completed_spans.append(len(observed.get_finished_spans()))
        if sdk == "sentry":
            assert observed == []  # Normal disconnects are not application exceptions.
        else:
            from opentelemetry import trace

            assert not trace.get_current_span().get_span_context().is_valid
            if completed_spans == [0, 0]:
                pytest.xfail(
                    "OTel Django middleware does not finish cancelled requests; "
                    "reproduced with plain Django and aiodrf"
                )
            assert completed_spans == [1, 2]


@pytest.mark.parametrize("sampled", [False, True])
@pytest.mark.parametrize("export_failure", [False, True])
@override_settings(ROOT_URLCONF=__name__, MIDDLEWARE=[], ALLOWED_HOSTS=["testserver"])
async def test_sampling_and_batch_export_failure(sampled, export_failure):
    from opentelemetry import trace
    from opentelemetry.instrumentation.django import DjangoInstrumentor
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExportResult
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )
    from opentelemetry.sdk.trace.sampling import ALWAYS_OFF, ALWAYS_ON

    calls = []

    class Exporter(InMemorySpanExporter):
        def export(self, spans):
            calls.append(threading.get_ident())
            if export_failure:
                return SpanExportResult.FAILURE
            return super().export(spans)

    exporter = Exporter()
    provider = TracerProvider(sampler=ALWAYS_ON if sampled else ALWAYS_OFF)
    provider.add_span_processor(
        BatchSpanProcessor(
            exporter,
            max_queue_size=16,
            max_export_batch_size=8,
            schedule_delay_millis=10,
        )
    )
    # Explicit application-owned provider: no repeated global provider registration.
    DjangoInstrumentor().instrument(tracer_provider=provider)
    try:
        application = get_asgi_application()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://testserver"
        ) as client:
            response = await client.get("/aiodrf/ok/", headers={"X-Test-User": "1"})
        assert response.status_code == 200
        assert response.json()["user"] == 1
        assert await asyncio.to_thread(provider.force_flush, timeout_millis=2000)
        # The global tracer in the application hooks is deliberately unconfigured;
        # this isolates the ordinary Django server span and its exporter.
        finished = exporter.get_finished_spans()
        assert len(finished) == int(sampled and not export_failure)
        assert bool(calls) is sampled
        assert all(thread != threading.get_ident() for thread in calls)
        assert not trace.get_current_span().get_span_context().is_valid
    finally:
        DjangoInstrumentor().uninstrument()
        await asyncio.to_thread(provider.shutdown)
