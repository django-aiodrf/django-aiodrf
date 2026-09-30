"""Submit diagnostic requests through the shared ASGI test driver."""

import json

from tests.asgi_driver import ASGIDriver, http_scope


async def call(application, method, url, payload=None):
    path, _, query = url.partition("?")
    body = json.dumps(payload).encode() if payload is not None else b""
    scope = http_scope(path, method=method)
    scope["query_string"] = query.encode()
    scope["headers"].extend(
        [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ]
    )
    async with ASGIDriver(application, scope) as driver:
        await driver.incoming.put(
            {"type": "http.request", "body": body, "more_body": False}
        )
        await driver.finish()
    return driver.sent[0]["status"]
