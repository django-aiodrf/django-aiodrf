"""Api contracts for the tasks django5 example."""

import pytest


@pytest.mark.parametrize("asynchronous", [True, False])
async def test_immediate_task(client, asynchronous):
    result = await client.post(
        "/enqueue/", json={"value": 4, "asynchronous": asynchronous}
    )
    assert result.status_code == 200
    assert result.json()["status"] == "SUCCESSFUL"
    assert result.json()["value"] == 8


async def test_dummy_queue_only_records(client):
    result = await client.post("/queue/", json={"value": 4})
    assert result.status_code == 202
    assert result.json()["status"] == "READY"
    assert "value" not in result.json()
    assert (await client.post("/enqueue/", json={"value": -1})).status_code == 400
