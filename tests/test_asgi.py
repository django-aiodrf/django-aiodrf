"""The lifespan protocol: startup and shutdown signals, and state that reaches requests."""

import asyncio

from django.test import TestCase, override_settings
from django.urls import path
from rest_framework.permissions import AllowAny

from aiodrf import signals
from aiodrf.asgi import LifespanApplication, get_asgi_application
from aiodrf.response import Response
from aiodrf.views import APIView


class State(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    async def get(self, request):
        return Response({"state": request.scope.get("state")})


urlpatterns = [path("state/", State.as_view())]


class Driver:
    """Drives an ASGI application through one lifespan connection."""

    def __init__(self, application):
        self.application = application
        self.incoming = asyncio.Queue()
        self.sent = []

    async def run(self, *messages, state=None):
        for message in messages:
            self.incoming.put_nowait(message)
        scope = {"type": "lifespan", "asgi": {"version": "3.0"}}
        if state is not None:
            scope["state"] = state
        await asyncio.wait_for(self.application(scope, self.incoming.get, self.send), 5)
        return [message["type"] for message in self.sent]

    async def send(self, message):
        self.sent.append(message)


class LifespanTests(TestCase):
    def setUp(self):
        self.calls = []

    def connect(self, signal, function):
        signal.connect(function)
        self.addCleanup(signal.disconnect, function)

    async def test_startup_and_shutdown_reach_async_and_sync_receivers(self):
        async def on_startup(sender, scope, **kwargs):
            self.calls.append(("startup", sender, scope["type"]))

        def on_shutdown(sender, scope, **kwargs):
            self.calls.append(("shutdown", sender, scope["type"]))

        self.connect(signals.asgi_startup, on_startup)
        self.connect(signals.asgi_shutdown, on_shutdown)
        driver = Driver(LifespanApplication(None))
        types = await driver.run(
            {"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}
        )
        assert types == ["lifespan.startup.complete", "lifespan.shutdown.complete"]
        assert self.calls == [
            ("startup", LifespanApplication, "lifespan"),
            ("shutdown", LifespanApplication, "lifespan"),
        ]

    async def test_a_failing_receiver_fails_the_phase_with_its_traceback(self):
        async def refuse(sender, scope, **kwargs):
            raise RuntimeError("no database")

        self.connect(signals.asgi_startup, refuse)
        driver = Driver(LifespanApplication(None))
        types = await driver.run({"type": "lifespan.startup"})
        assert types == ["lifespan.startup.failed"]
        assert "RuntimeError: no database" in driver.sent[0]["message"]

    @override_settings(ROOT_URLCONF=__name__)
    async def test_state_set_at_startup_reaches_requests(self):
        async def open_resources(sender, scope, **kwargs):
            scope["state"]["pool"] = "ready"

        self.connect(signals.asgi_startup, open_resources)
        application = get_asgi_application()
        state = {}
        types = await Driver(application).run(
            {"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}, state=state
        )
        assert types == ["lifespan.startup.complete", "lifespan.shutdown.complete"]
        assert state == {"pool": "ready"}

        # A server copies the lifespan state into every request's scope.
        messages = []
        body_sent = False

        async def receive():
            nonlocal body_sent
            if body_sent:
                await asyncio.Event().wait()  # Django listens for a disconnect
            body_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "path": "/state/",
            "raw_path": b"/state/",
            "query_string": b"",
            "headers": [(b"host", b"testserver")],
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 1),
            "scheme": "http",
            "state": dict(state),
        }
        await asyncio.wait_for(application(scope, receive, send), 5)
        assert messages[0]["status"] == 200
        assert b'"pool":"ready"' in messages[1]["body"]

    async def test_other_connections_go_to_the_wrapped_application(self):
        seen = []

        async def wrapped(scope, receive, send):
            seen.append(scope["type"])

        await LifespanApplication(wrapped)({"type": "http"}, None, None)
        assert seen == ["http"]

    async def test_resources_are_live_between_startup_and_shutdown(self):
        class Resource:
            def __init__(self):
                self.loop = asyncio.get_running_loop()
                self.closed = False

            async def use(self):
                assert not self.closed
                assert asyncio.get_running_loop() is self.loop

            async def aclose(self):
                await self.use()
                self.closed = True

        async def startup(sender, scope, **kwargs):
            scope["state"]["resource"] = Resource()

        async def shutdown(sender, scope, **kwargs):
            await scope["state"]["resource"].aclose()

        async def application(scope, receive, send):
            await scope["state"]["resource"].use()

        self.connect(signals.asgi_startup, startup)
        self.connect(signals.asgi_shutdown, shutdown)
        wrapper = LifespanApplication(application)
        state = {}
        incoming = asyncio.Queue()
        outgoing = asyncio.Queue()
        scope = {"type": "lifespan", "asgi": {"version": "3.0"}, "state": state}
        task = asyncio.create_task(wrapper(scope, incoming.get, outgoing.put))
        try:
            await incoming.put({"type": "lifespan.startup"})
            assert (await asyncio.wait_for(outgoing.get(), 2))[
                "type"
            ] == "lifespan.startup.complete"
            await wrapper({"type": "http", "state": dict(state)}, None, None)
            assert not state["resource"].closed
            await incoming.put({"type": "lifespan.shutdown"})
            assert (await asyncio.wait_for(outgoing.get(), 2))[
                "type"
            ] == "lifespan.shutdown.complete"
            await asyncio.wait_for(task, 2)
            assert state["resource"].closed
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
