"""Api contracts for the list enrichment example."""


async def test_batch_and_concurrent_output_match(client):
    expected = [{"number": n, "value": n * 10} for n in range(4)]
    assert (await client.get("/batch/")).json() == expected
    assert (await client.get("/concurrent/")).json() == expected
