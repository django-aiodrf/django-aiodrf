"""Opt-in native-backend contracts; run in the isolated benchmark environment."""

import asyncio

import httpx
import pytest

from tests.deployment.processes import deployment
from tests.deployment.views import AuthorSerializer
from tests.testapp.models import Author

pytest.importorskip("django_async_backend")


def test_native_queryset_requires_explicit_materialization_for_drf():
    from django_async_backend.db.models.manager import AsyncManager

    manager = AsyncManager()
    manager.model = Author
    # No model patch or database access in this negative compatibility check.
    with pytest.raises(TypeError, match="not iterable synchronously"):
        _ = AuthorSerializer(manager.all(), many=True).data


async def test_native_reads_preserve_payload_authentication_and_return_pool_leases(
    tmp_path,
):
    async with (
        deployment(tmp_path, workers=1, pool_size=2, db_backend="async") as service,
        httpx.AsyncClient(cookies=service.cookies, timeout=10) as client,
        httpx.AsyncClient(timeout=10) as anonymous,
    ):
        expected = {}
        for suffix in ("", "1/"):
            response = await client.get(f"{service.url}/drf-read/{suffix}")
            assert response.status_code == 200
            expected[suffix] = response.content
            for route in ("drf-read", "aiodrf-read", "native-read"):
                url = f"{service.url}/{route}/{suffix}"
                assert (await anonymous.get(url)).status_code == 403
                response = await client.get(url)
                assert response.status_code == 200
                assert response.content == expected[suffix]

        before = (await client.get(service.url + "/metrics/")).json()["native_pool"]

        async def read_many():
            for _ in range(8):
                for suffix in ("", "1/"):
                    response = await client.get(f"{service.url}/native-read/{suffix}")
                    assert response.status_code == 200
                    assert response.content == expected[suffix]

        async with asyncio.TaskGroup() as tasks:
            for _ in range(8):
                tasks.create_task(read_many())

        # An HTTP response can arrive just before the request_finished receiver.
        async with asyncio.timeout(3):
            while True:
                after = (await client.get(service.url + "/metrics/")).json()[
                    "native_pool"
                ]
                if after["pool_available"] == after["pool_size"]:
                    break
                await asyncio.sleep(0.01)
        assert after["requests_num"] - before["requests_num"] == 128
        assert after["requests_waiting"] == 0
        assert after.get("requests_errors", 0) == 0
        assert after["pool_size"] <= 2

    assert not service.created_database
    assert not service.created_container
    assert service.server.poll() is not None
    assert len(service.records("closed")) == 1
