"""Api contracts for the basics example."""


async def test_hooks_and_sync_coexistence(client):
    response = await client.post("/echo/", json={"name": "Ada"})
    assert response.json() == {"name": "Ada"}
    assert response.headers["X-Example"] == "basics"
    assert (await client.post("/echo/", json={"name": "reserved"})).status_code == 400
    assert (await client.get("/sync/")).json() == {"transport": "sync DRF"}
    assert (await client.get("/decorated/")).status_code == 200
