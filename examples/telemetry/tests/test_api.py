"""Api contracts for the telemetry example."""


async def test_traced_response(client):
    assert (await client.get("/traced/")).json() == {"traced": True}
