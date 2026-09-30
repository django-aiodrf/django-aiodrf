"""Api contracts for the mongodb example."""

import pytest

pytestmark = pytest.mark.django_db(transaction=True)


async def test_objectid_response_and_bad_lookup(client):
    result = await client.post("/notes/", json={"title": "Mongo"})
    assert result.status_code == 201
    assert isinstance(result.json()["id"], str)
    assert len(result.json()["id"]) == 24
    assert (await client.get("/notes/not-an-objectid/")).status_code == 404


async def test_direct_driver_and_orm_share_documents(client):
    created = await client.post("/native-notes/", json={"title": "Native"})
    assert created.status_code == 201, created.text
    identifier = created.json()["id"]
    through_orm = await client.get(f"/notes/{identifier}/")
    assert through_orm.json() == created.json()
    assert (await client.post("/native-notes/", json={"title": ""})).status_code == 400
    rows = (await client.get("/native-notes/")).json()
    assert created.json() in rows
