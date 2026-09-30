"""A saturated Django psycopg pool has a bounded wait and recovers after release."""

import asyncio
import copy
import threading

import pytest
from asgiref.sync import ThreadSensitiveContext
from django.db import OperationalError, connections
from django.db.backends.postgresql.base import DatabaseWrapper

from aiodrf.utils import run_sync

pytestmark = pytest.mark.django_db(transaction=True)


async def test_pool_exhaustion_timeout_release_and_cancelled_waiter(worker_connections):  # noqa: PLR0915 -- one owned pool lifecycle
    config = copy.deepcopy(connections["default"].settings_dict)
    assert config["ENGINE"] == "django.db.backends.postgresql"
    config.update(
        CONN_MAX_AGE=0, OPTIONS={"pool": {"min_size": 1, "max_size": 1, "timeout": 0.3}}
    )
    alias = "aiodrf_saturation_contract"
    held, release = threading.Event(), threading.Event()
    waiter_entered, waiter_done = threading.Event(), threading.Event()
    outcomes = []

    def query(*, hold=False, waiting=False):
        wrapper = DatabaseWrapper(config, alias)
        try:
            if waiting:
                waiter_entered.set()
            with wrapper.cursor() as cursor:
                cursor.execute("SELECT 1")
                assert cursor.fetchone() == (1,)
                if hold:
                    held.set()
                    assert release.wait(5), (
                        "controller must release the owned connection"
                    )
        except OperationalError as exc:
            outcomes.append(exc)
            raise
        finally:
            wrapper.close()
            if waiting:
                waiter_done.set()

    async def in_request(**kwargs):
        async with ThreadSensitiveContext():
            await run_sync(query)(**kwargs)

    holder = asyncio.create_task(in_request(hold=True))
    waiter = None
    try:
        assert await asyncio.to_thread(held.wait, 3)
        # A separate request thread must not serialize behind the holder.
        with pytest.raises(OperationalError, match="couldn't get a connection"):
            await asyncio.wait_for(in_request(), timeout=2)
        waiter = asyncio.create_task(in_request(waiting=True))
        assert await asyncio.to_thread(waiter_entered.wait, 2)
        waiter.cancel()
        # Cancelling a coroutine cannot interrupt the synchronous pool wait.
        # Its finite pool timeout releases the request worker on context exit.
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(waiter, timeout=2)
        assert waiter_done.is_set()
        assert len(outcomes) == 2
        release.set()
        await asyncio.wait_for(holder, timeout=2)
        await asyncio.wait_for(in_request(), timeout=2)
        pool = DatabaseWrapper(config, alias).pool
        assert pool.get_stats()["pool_available"] == 1
    finally:
        release.set()
        if waiter is not None:
            await asyncio.gather(waiter, return_exceptions=True)
        await asyncio.gather(holder, return_exceptions=True)
        await run_sync(DatabaseWrapper(config, alias).close_pool)()
