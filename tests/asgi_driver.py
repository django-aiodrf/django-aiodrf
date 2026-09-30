"""Queue-driven ASGI connections with deterministic backpressure and shutdown."""

import asyncio


def http_scope(path="/", *, method="GET", state=None):
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "path": path,
        "query_string": b"",
        "headers": [(b"host", b"testserver")],
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 1),
        "scheme": "http",
    }
    if state is not None:
        scope["state"] = state
    return scope


class ASGIDriver:
    def __init__(self, application, scope, *, block_body=False):
        self.application = application
        self.scope = scope
        self.incoming = asyncio.Queue()
        self.outgoing = asyncio.Queue()
        self.sent = []
        self.block_body = block_body
        self.blocked = asyncio.Event()
        self.release_send = asyncio.Event()

    async def __aenter__(self):
        self.task = asyncio.create_task(
            self.application(self.scope, self.incoming.get, self.send)
        )
        return self

    async def __aexit__(self, *exc):
        if not self.task.done():
            self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)

    async def send(self, message):
        self.sent.append(message)
        await self.outgoing.put(message)
        if (
            self.block_body
            and message["type"] == "http.response.body"
            and message.get("body")
        ):
            self.blocked.set()
            await self.release_send.wait()

    async def receive(self):
        return await asyncio.wait_for(self.outgoing.get(), 3)

    async def finish(self):
        await asyncio.wait_for(self.task, 3)
