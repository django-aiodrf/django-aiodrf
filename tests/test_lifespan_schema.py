"""Schema/check commands must not require an ASGI-owned runtime resource."""

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from io import StringIO
from types import ModuleType

from django.core.management import call_command
from django.test import override_settings
from rest_framework import serializers
from rest_framework.decorators import action

from aiodrf.asgi import LifespanApplication, get_lifespan_state
from aiodrf.response import Response
from aiodrf.routers import SimpleRouter
from aiodrf.utils import run_sync
from aiodrf.viewsets import GenericViewSet
from tests.asgi_driver import ASGIDriver, http_scope


@dataclass
class Resources:
    name: str = "live"


class ResourceSerializer(serializers.Serializer):
    name = serializers.CharField()


class EchoSerializer(serializers.Serializer):
    value = serializers.IntegerField()


async def test_offline_schema_matches_active_and_closed_lifespans_without_opening_resources():
    events = []

    @asynccontextmanager
    async def lifespan():
        events.append("open")
        try:
            yield Resources()
        finally:
            events.append("close")

    class View(GenericViewSet):
        authentication_classes = []
        permission_classes = []
        serializer_class = ResourceSerializer

        def get_serializer_class(self):
            # A runtime-dependent choice needs spectacular's explicit offline guard.
            if not getattr(self, "swagger_fake_view", False):
                get_lifespan_state(self.request, Resources)
            return super().get_serializer_class()

        async def list(self, request):
            resource = get_lifespan_state(request, Resources)
            return Response({"name": resource.name})

        @action(detail=False, methods=["post"], serializer_class=EchoSerializer)
        async def echo(self, request):
            serializer = self.get_serializer(data=await request.adata())
            serializer.is_valid(raise_exception=True)
            return Response(serializer.validated_data)

    router = SimpleRouter()
    router.register("resources", View, basename="resources")
    urls = ModuleType("lifespan_schema_urls")
    urls.urlpatterns = router.urls

    def schema():
        out = StringIO()
        call_command(
            "spectacular",
            "--format",
            "openapi-json",
            "--validate",
            "--fail-on-warn",
            stdout=out,
        )
        call_command("check", tags=["compatibility"], stdout=StringIO())
        return json.loads(out.getvalue())

    with override_settings(ROOT_URLCONF=urls, AIODRF={"LIFESPAN": lifespan}):
        before = await run_sync(schema)()
        assert events == []
        from django.core.asgi import get_asgi_application

        app = LifespanApplication(get_asgi_application(), lifespan=lifespan)
        state = {}
        async with ASGIDriver(app, {"type": "lifespan", "state": state}) as driver:
            await driver.incoming.put({"type": "lifespan.startup"})
            assert (await driver.receive())["type"] == "lifespan.startup.complete"
            active = await run_sync(schema)()
            assert events == ["open"]
            async with ASGIDriver(
                app, http_scope("/resources/", state=dict(state))
            ) as request:
                await request.incoming.put({"type": "http.request", "body": b""})
                await request.finish()
                assert request.sent[0]["status"] == 200
            await driver.incoming.put({"type": "lifespan.shutdown"})
            assert (await driver.receive())["type"] == "lifespan.shutdown.complete"
            await driver.finish()
        after = await run_sync(schema)()
        assert before == active == after
        assert events == ["open", "close"]
    operation = before["paths"]["/resources/echo/"]["post"]
    assert (
        "Echo"
        in operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    )
