"""Api contracts for the typed schemas example."""

import pytest


@pytest.mark.parametrize("backend", ["msgspec", "pydantic"])
async def test_input_partial_and_errors(client, backend):
    assert (await client.post(f"/{backend}/", json={"name": "Ada"})).json() == {
        "name": "Ada",
        "count": 1,
        "accepted": True,
    }
    assert (await client.patch(f"/{backend}/", json={"count": 3})).json() == {
        "count": 3
    }
    assert (await client.post(f"/{backend}/", json={})).status_code == 400


async def test_query_and_schema(client):
    assert (await client.get("/query/?limit=3")).json() == {"limit": 3}
    assert (await client.get("/query/?limit=0")).status_code == 400
    assert (await client.get("/schema/")).status_code == 200
    assert (await client.get("/docs/")).status_code == 200


async def test_native_context_hooks_and_root_shape(client):
    response = await client.post("/pydantic-context/", json={"name": " Ada "})
    assert response.json() == {"name": "Hello, Ada"}
    assert (
        await client.post("/pydantic-context/", json={"name": " "})
    ).status_code == 400
    response = await client.post("/msgspec-custom/", json={"reference": "book:42"})
    assert response.json() == {"reference": "book:42"}
    assert (
        await client.post("/msgspec-custom/", json={"reference": 42})
    ).status_code == 400
    response = await client.post("/root-list/", json=[1, 2, 3])
    assert response.json() == [1, 2, 3]
    assert (await client.post("/root-list/", json=["invalid"])).status_code == 400


async def test_custom_type_and_root_schemas_match_the_wire_contract(client):
    schema = (await client.get("/schema/?format=json")).json()
    components = schema["components"]["schemas"]
    operation = schema["paths"]["/msgspec-custom/"]["post"]
    ref = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    assert (
        components[ref.rsplit("/", 1)[1]]["properties"]["reference"]["type"] == "string"
    )
    operation = schema["paths"]["/root-list/"]["post"]
    ref = operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    assert components[ref.rsplit("/", 1)[1]]["type"] == "array"
