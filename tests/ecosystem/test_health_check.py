"""django-health-check served next to aiodrf views by aiodrf's ASGI application."""

import asyncio
import gc
import warnings

import httpx
import pytest
from asgi_lifespan import LifespanManager
from django.test import override_settings
from django.urls import path
from health_check.views import HealthCheckView
from rest_framework.permissions import AllowAny

from aiodrf.asgi import get_asgi_application
from aiodrf.response import Response
from aiodrf.views import APIView

CHECKS = ("health_check.checks.Cache", "health_check.checks.Database")


class Ping(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    async def get(self, request):
        return Response({"pong": True})


urlpatterns = [
    path("health/", HealthCheckView.as_view(checks=CHECKS)),
    path(
        "health/broken/",
        HealthCheckView.as_view(
            checks=[("health_check.checks.Database", {"alias": "missing"})]
        ),
    ),
    path("ping/", Ping.as_view()),
]


@pytest.mark.django_db(transaction=True)
@override_settings(ROOT_URLCONF=__name__)
async def test_health_checks_run_beside_aiodrf_views():
    async with LifespanManager(get_asgi_application()) as manager:
        transport = httpx.ASGITransport(app=manager.app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            health = await client.get("/health/", params={"format": "json"})
            assert health.status_code == 200, health.text
            assert set(health.json().values()) == {"OK"}
            assert (await client.get("/ping/")).json() == {"pong": True}
            broken = await client.get("/health/broken/", params={"format": "json"})
            assert broken.status_code == 500
            assert list(broken.json().values()) == [
                "Unavailable: Database alias does not exist"
            ]
    # The database check ran in the loop's default executor. Django's
    # in-memory test database ignores ``close()``, so that thread's connection
    # is only released when the thread ends: end it here, not in a later test.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ResourceWarning)
        await asyncio.get_running_loop().shutdown_default_executor()
        gc.collect()
