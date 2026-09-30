"""Api contracts for the streaming example."""

import json


async def test_each_wire_format(client):
    lines = await client.get("/ndjson/")
    assert lines.headers["content-type"].startswith("application/x-ndjson")
    expected = [{"number": n} for n in range(3)]
    assert [json.loads(line) for line in lines.text.splitlines()] == expected
    assert (await client.get("/array/")).json() == expected
    events = await client.get("/events/")
    assert events.headers["content-type"].startswith("text/event-stream")
    assert events.text.count("event: number") == 3
