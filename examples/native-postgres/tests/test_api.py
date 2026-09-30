"""Api contracts for the native postgres example."""

import pytest

pytestmark = pytest.mark.django_db(transaction=True)


async def test_native_crud(client):
    created = await client.post("/notes/", json={"title": "Native"})
    assert created.status_code == 201
    pk = created.json()["id"]
    assert (
        await client.patch(f"/notes/{pk}/", json={"title": "Changed"})
    ).status_code == 200
    assert (await client.get("/notes/")).json() == [{"id": pk, "title": "Changed"}]
    assert (await client.delete(f"/notes/{pk}/")).status_code == 204
