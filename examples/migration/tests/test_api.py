"""Api contracts for the migration example."""


async def test_legacy_async_property(client):
    assert (await client.get("/legacy/")).json() == {"name": "legacy"}
