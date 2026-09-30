"""Api contracts for the middleware experiment example."""


async def test_security_header_is_preserved(client):
    response = await client.get("/headers/")
    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
