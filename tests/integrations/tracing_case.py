"""
``aiodrf.contrib.opentelemetry`` phase spans. Run only in a fresh subprocess:
the SDK and Django's instrumentation change global state.
"""

import asyncio

import httpx
import pytest
from django.core.asgi import get_asgi_application
from django.test import override_settings
from django.urls import path
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

from aiodrf.contrib.opentelemetry import TracingMixin
from tests.asgi_driver import ASGIDriver, http_scope
from tests.integrations.observability_case import Endpoint, cancelled

PHASES = [
    "aiodrf.authenticate",
    "aiodrf.check_permissions",
    "aiodrf.check_throttles",
    "aiodrf.handler",
    "aiodrf.finalize",
]


class Traced(TracingMixin, Endpoint):
    pass


class DenyAll(BasePermission):
    def has_permission(self, request, view):
        return False


class Denied(TracingMixin, Endpoint):
    permission_classes = [DenyAll]


class TenantEndpoint(Endpoint):
    # Written for DRF: a synchronous check on the view the mixin is added to.
    def check_permissions(self, request):
        raise PermissionDenied("Tenant access denied")


class TracedTenant(TracingMixin, TenantEndpoint):
    pass


class TracedCancel(TracingMixin, Endpoint):
    async def get(self, request, failure):
        return await cancelled(request._request)


urlpatterns = [
    path("traced/<str:failure>/", Traced.as_view()),
    path("denied/<str:failure>/", Denied.as_view()),
    path("cancel/<str:failure>/", TracedCancel.as_view()),
    path("tenant/<str:failure>/", TracedTenant.as_view()),
]
settings = override_settings(
    ROOT_URLCONF=__name__, MIDDLEWARE=[], ALLOWED_HOSTS=["testserver"]
)


def provider_with(exporter, sampler=None):
    from opentelemetry import trace
    from opentelemetry.instrumentation.django import DjangoInstrumentor
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    provider = TracerProvider(sampler=sampler) if sampler else TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    DjangoInstrumentor().instrument(tracer_provider=provider)
    return provider


def children(spans, parent):
    return [s for s in spans if s.parent and s.parent.span_id == parent.context.span_id]


async def get(application, url, user):
    transport = httpx.ASGITransport(app=application, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        return await client.get(url, headers={"X-Test-User": str(user)})


@settings
async def test_phases_are_children_of_their_own_server_span():
    from opentelemetry import trace
    from opentelemetry.instrumentation.django import DjangoInstrumentor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    provider_with(exporter)
    try:
        application = get_asgi_application()
        requests = [
            ("traced/ok", 41, 200),
            ("traced/worker", 42, 500),
            ("traced/view", 43, 500),
            ("denied/ok", 44, 403),
        ] * 2
        responses = await asyncio.gather(
            *(get(application, f"/{url}/", user) for url, user, _ in requests)
        )
        assert [r.status_code for r in responses] == [status for *_, status in requests]
        spans = exporter.get_finished_spans()
        servers = [s for s in spans if s.kind is trace.SpanKind.SERVER]
        assert len(servers) == len(requests)
        for server in servers:
            phases = {s.name: s for s in children(spans, server)}
            url = server.attributes["http.route"]
            view = "Denied" if url.startswith("denied") else "Traced"
            for span in phases.values():
                assert span.context.trace_id == server.context.trace_id
                assert span.attributes["aiodrf.view"].endswith(view)
                # No request data in the attributes.
                assert set(span.attributes) <= {
                    "aiodrf.view",
                    "aiodrf.action",
                    "aiodrf.exception",
                    "aiodrf.cancelled",
                }
            if view == "Denied":
                assert list(phases) == [*PHASES[:2], "aiodrf.finalize"]
                denied = phases["aiodrf.check_permissions"]
                assert denied.attributes["aiodrf.exception"] == "PermissionDenied"
                assert denied.status.status_code is trace.StatusCode.UNSET
                continue
            status = server.attributes["http.status_code"]
            handler = phases["aiodrf.handler"]
            # The application's own spans nest under the handler, across the hop.
            (own,) = children(spans, handler)
            assert own.name == "view"
            if status == 200:
                assert list(phases) == PHASES
                assert handler.status.status_code is trace.StatusCode.UNSET
            else:
                # An unexpected error propagates past finalization, as in DRF.
                assert list(phases) == PHASES[:4]
                assert handler.status.status_code is trace.StatusCode.ERROR
                assert [e.name for e in handler.events] == ["exception"]
        assert not trace.get_current_span().get_span_context().is_valid
    finally:
        DjangoInstrumentor().uninstrument()


@settings
async def test_an_inherited_synchronous_check_runs_in_its_span():
    from opentelemetry.instrumentation.django import DjangoInstrumentor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    provider_with(exporter)
    try:
        response = await get(get_asgi_application(), "/tenant/ok/", 1)
        assert response.status_code == 403
        names = {s.name: s for s in exporter.get_finished_spans()}
        denied = names["aiodrf.check_permissions"]
        assert denied.attributes["aiodrf.exception"] == "PermissionDenied"
        assert "aiodrf.handler" not in names
    finally:
        DjangoInstrumentor().uninstrument()


@settings
async def test_a_cancelled_handler_ends_its_span():
    from opentelemetry.instrumentation.django import DjangoInstrumentor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    provider_with(exporter)
    try:
        application = get_asgi_application()
        state = {"entered": asyncio.Event(), "closed": asyncio.Event()}
        scope = http_scope("/cancel/ok/", state=state)
        scope["headers"].append((b"x-test-user", b"1"))
        async with ASGIDriver(application, scope) as driver:
            await driver.incoming.put({"type": "http.request", "body": b""})
            await asyncio.wait_for(state["entered"].wait(), 3)
            await driver.incoming.put({"type": "http.disconnect"})
            await driver.finish()
        names = {s.name: s for s in exporter.get_finished_spans()}
        assert names["aiodrf.handler"].attributes["aiodrf.cancelled"] is True
        # The server span of Django's instrumentation is the known exception
        # (K05 in docs/open-work.md); the phases do not hide it.
        if not any(s.kind.name == "SERVER" for s in exporter.get_finished_spans()):
            pytest.xfail(
                "Django's OTel middleware does not finish cancelled requests (K05)"
            )
    finally:
        DjangoInstrumentor().uninstrument()


@settings
async def test_nothing_is_recorded_when_sampling_is_off():
    from opentelemetry.instrumentation.django import DjangoInstrumentor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )
    from opentelemetry.sdk.trace.sampling import ALWAYS_OFF

    exporter = InMemorySpanExporter()
    provider_with(exporter, ALWAYS_OFF)
    try:
        response = await get(get_asgi_application(), "/traced/ok/", 1)
        assert response.status_code == 200
        assert exporter.get_finished_spans() == ()
    finally:
        DjangoInstrumentor().uninstrument()


@settings
async def test_without_a_configured_provider_the_phases_cost_nothing_visible():
    # opentelemetry-api's default provider is a no-op; the view just works.
    response = await get(get_asgi_application(), "/traced/ok/", 1)
    assert response.status_code == 200
    assert response.json()["user"] == 1
