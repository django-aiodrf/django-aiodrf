"""Api contracts for the crud example."""

import pytest

pytestmark = pytest.mark.django_db(transaction=True)


async def test_crud_filter_and_each_paginator(client):
    created = await client.post("/articles/", json={"title": "First", "tags": []})
    assert created.status_code == 201
    pk = created.json()["id"]
    for endpoint in ("articles", "offset", "cursor"):
        body = (await client.get(f"/{endpoint}/?search=First")).json()
        assert [item["id"] for item in body["results"]] == [pk]
    assert (await client.get("/articles/?title=missing")).json()["count"] == 0
    assert (
        await client.patch(f"/articles/{pk}/", json={"title": "Updated"})
    ).status_code == 200
    assert (await client.delete(f"/articles/{pk}/")).status_code == 204
    assert (await client.get(f"/articles/{pk}/")).status_code == 404
