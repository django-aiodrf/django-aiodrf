"""Wire delivery through real Nginx; header presence alone is not a stream test."""

import asyncio
import json
import ssl
import time

import httpx
import pytest

from tests.deployment.processes import deployment


@pytest.mark.parametrize("route", ["stream", "ndjson"])
async def test_buffering_overrides_compression_and_first_item(
    tmp_path, record_property, route
):
    observations = []
    async with (
        deployment(tmp_path, workers=1) as service,
        httpx.AsyncClient(
            cookies=service.cookies, timeout=5, trust_env=False
        ) as client,
    ):
        for prefix in (
            "",
            "/header-buffered",
            "/buffered",
            "/compressed",
            "/compressed-buffered",
        ):
            for buffering in (None, "no", "yes"):
                params = {"limit": 2, "interval": 0.6}
                if buffering is not None:
                    params["buffering"] = buffering
                started = time.monotonic()
                events, arrivals = [], []
                async with client.stream(
                    "GET",
                    service.url + prefix + f"/{route}/",
                    params=params,
                    headers={"Accept-Encoding": "gzip"},
                ) as response:
                    assert response.status_code == 200
                    media_type = (
                        "text/event-stream"
                        if route == "stream"
                        else "application/x-ndjson"
                    )
                    assert response.headers["content-type"].startswith(media_type)
                    assert response.headers.get("cache-control") == (
                        "no-cache" if route == "stream" else None
                    )
                    compressed = prefix.startswith("/compressed")
                    assert (
                        response.headers.get("content-encoding") == "gzip"
                    ) is compressed
                    async for line in response.aiter_lines():
                        if line and (route == "ndjson" or line.startswith("data:")):
                            events.append(
                                json.loads(line if route == "ndjson" else line[5:])
                            )
                            arrivals.append(time.monotonic() - started)
                assert events[0]["ready"] is True
                assert [event["sequence"] for event in events[1:]] == [0, 1]
                ignores_header = prefix == "/buffered"
                unbuffered = not ignores_header and (
                    buffering == "no"
                    or (buffering is None and prefix in ("", "/compressed"))
                )
                if unbuffered:
                    # Delivered separately, before the delayed producer finishes.
                    # No universal latency promise for buffering-enabled paths.
                    assert arrivals[-1] - arrivals[0] > 0.6, arrivals
                observations.append(
                    {
                        "path": prefix or "/",
                        "header": buffering,
                        "gzip": compressed,
                        "arrival_seconds": arrivals,
                    }
                )
        async with asyncio.timeout(3):
            while True:
                metrics = (await client.get(service.url + "/metrics/")).json()
                if metrics["opened"] == metrics["closed"] == len(observations):
                    break
                await asyncio.sleep(0.01)
        record_property("nginx", service.nginx_version)
        record_property("delivery", json.dumps(observations))


@pytest.mark.parametrize("route", ["stream", "ndjson"])
async def test_streams_over_tls_and_http2(tmp_path, record_property, route):
    # The same streams through Nginx terminating TLS and speaking HTTP/2 to the
    # client (HTTP/1.1 to Uvicorn): items arrive as produced, and a client that
    # resets its stream closes the producer.
    pytest.importorskip("h2")
    async with (
        deployment(tmp_path, workers=1, tls=True) as service,
        httpx.AsyncClient(
            http2=True,
            verify=ssl.create_default_context(cafile=service.certificate),
            cookies=service.cookies,
            timeout=5,
            trust_env=False,
        ) as client,
    ):
        started = time.monotonic()
        events, arrivals = [], []
        url = service.tls_url + f"/{route}/"
        async with client.stream(
            "GET", url, params={"limit": 2, "interval": 0.6}
        ) as response:
            assert response.status_code == 200
            assert response.http_version == "HTTP/2"
            async for line in response.aiter_lines():
                if line and (route == "ndjson" or line.startswith("data:")):
                    events.append(json.loads(line if route == "ndjson" else line[5:]))
                    arrivals.append(time.monotonic() - started)
        assert events[0]["ready"] is True
        assert [event["sequence"] for event in events[1:]] == [0, 1]
        assert arrivals[-1] - arrivals[0] > 0.6, arrivals

        # A browser's EventSource.close() on HTTP/2 resets the stream and
        # keeps the connection. (httpx does not reset a stream it leaves.)
        async def producers_closed():
            async with asyncio.timeout(5):
                while True:
                    metrics = (await client.get(service.tls_url + "/metrics/")).json()
                    if metrics["opened"] == metrics["closed"] == 2:
                        return
                    await asyncio.sleep(0.05)

        await reset_after_first_item(
            service, f"/{route}/?limit=100&interval=0.2", producers_closed
        )
        record_property("nginx", service.nginx_version)


async def reset_after_first_item(service, path, check):
    """
    Open a stream over HTTP/2, read its first chunk, send RST_STREAM(CANCEL)
    and run ``check`` while the connection is still open.
    """
    import h2.config
    import h2.connection
    import h2.events

    context = ssl.create_default_context(cafile=service.certificate)
    context.set_alpn_protocols(["h2"])
    host, port = service.tls_url.removeprefix("https://").split(":")
    reader, writer = await asyncio.open_connection(host, int(port), ssl=context)
    connection = h2.connection.H2Connection(h2.config.H2Configuration(client_side=True))
    connection.initiate_connection()
    cookie = "; ".join(f"{name}={value}" for name, value in service.cookies.items())
    connection.send_headers(
        1,
        [(":method", "GET"), (":path", path), (":scheme", "https"),
         (":authority", host), ("cookie", cookie)],
        end_stream=True,
    )  # fmt: skip
    writer.write(connection.data_to_send())
    await writer.drain()
    try:
        async with asyncio.timeout(5):
            while True:
                events = connection.receive_data(await reader.read(65535))
                writer.write(connection.data_to_send())
                if any(isinstance(event, h2.events.DataReceived) for event in events):
                    break
        connection.reset_stream(1, h2.errors.ErrorCodes.CANCEL)
        writer.write(connection.data_to_send())
        await writer.drain()
        await check()
    finally:
        writer.close()
        await writer.wait_closed()
