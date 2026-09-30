"""
``TracingMixin`` must not change what a view does: every phase it wraps runs
the implementation the view would run without it, synchronous overrides
inherited from the view's base included. No tracer provider is set here
(the SDK is tested in ``tests/integrations/test_tracing.py``); the mixin
changes nothing then, dispatch included.
"""

import pytest
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied, Throttled

from aiodrf.response import Response
from aiodrf.test import AsyncAPIRequestFactory, count_hops
from aiodrf.views import APIView

pytest.importorskip("opentelemetry")

from aiodrf.contrib.opentelemetry import TracingMixin


class Open(APIView):
    authentication_classes = []
    permission_classes = []

    async def get(self, request):
        return Response({"private": True})


class SyncAuthentication(Open):
    def perform_authentication(self, request):
        raise AuthenticationFailed("no tenant")


class SyncPermissions(Open):
    def check_permissions(self, request):
        raise PermissionDenied("Tenant access denied")


class SyncThrottles(Open):
    def check_throttles(self, request):
        raise Throttled(wait=7)


class SyncFinalize(Open):
    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["X-Finalized"] = "sync"
        return response


class AsyncPermissions(Open):
    async def acheck_permissions(self, request):
        raise PermissionDenied("async denial")


@pytest.mark.parametrize(
    "base",
    [
        SyncAuthentication,
        SyncPermissions,
        SyncThrottles,
        SyncFinalize,
        AsyncPermissions,
        Open,
    ],
)
async def test_tracing_runs_what_the_view_would_run(base):
    traced = type(f"Traced{base.__name__}", (TracingMixin, base), {})
    responses, hops = [], []
    for view in (base, traced):
        with count_hops() as counted:
            responses.append(await view.as_view()(AsyncAPIRequestFactory().get("/")))
        hops.append(counted.count)
    plain, with_tracing = responses
    assert hops[0] == hops[1]
    assert with_tracing.status_code == plain.status_code
    assert with_tracing.data == plain.data
    assert with_tracing.get("X-Finalized") == plain.get("X-Finalized")
    assert with_tracing.get("Retry-After") == plain.get("Retry-After")
