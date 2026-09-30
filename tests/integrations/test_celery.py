"""Real Redis broker + Celery worker, without eager execution or a task adapter."""

import asyncio
import contextvars
import gc
import os
import threading
import uuid
from contextlib import ExitStack

import pytest
import redis
from asgiref.sync import ThreadSensitiveContext
from celery import Celery
from celery.contrib.testing.worker import start_worker
from celery.result import allow_join_result
from django.db import transaction
from django.utils.asyncio import async_unsafe
from kombu import Producer
from kombu.exceptions import OperationalError

from aiodrf.utils import run_sync

tenant = contextvars.ContextVar("celery_contract_tenant", default=None)


@pytest.fixture(scope="module")
def queue(django_db_setup, django_db_blocker):
    url = os.environ.get("AIODRF_TEST_CELERY_BROKER", "redis://127.0.0.1:6380/13")
    prefix = f"aiodrf-hardening-{uuid.uuid4().hex}:"
    client = redis.Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)
    client.ping()  # A missing broker is a failed integration run, not a silent skip.
    app = Celery("aiodrf-contract", broker=url, backend=url, set_as_current=False)
    app.conf.update(
        task_default_queue=prefix + "jobs",
        broker_transport_options={"global_keyprefix": prefix},
        result_backend_transport_options={"global_keyprefix": prefix},
        result_backend_thread_safe=True,
        result_expires=60,
        task_always_eager=False,
        worker_enable_remote_control=False,
        broker_connection_retry_on_startup=False,
    )

    @app.task(name="aiodrf.contract.echo")
    def echo(value):
        return {"value": value, "thread": threading.get_ident()}

    try:
        with ExitStack() as stack:
            # Celery's Django fixup runs model checks during worker startup.
            with django_db_blocker.unblock():
                stack.enter_context(
                    start_worker(
                        app, pool="solo", perform_ping_check=False, shutdown_timeout=10
                    )
                )
            yield echo
    finally:
        app.producer_pool.force_close_all()
        app.pool.force_close_all()
        app.backend.result_consumer.stop()
        app.backend.client.connection_pool.disconnect()
        app.close()
        # Never FLUSHDB: this database may hold another local test's keys.
        keys = list(client.scan_iter(match=prefix + "*"))
        if keys:
            client.delete(*keys)
        assert not list(client.scan_iter(match=prefix + "*"))
        client.close()
        # A connection this module left open is reported here, not at a
        # later test's collection (a 2026-09-24 run reported one in
        # test_celery_prefork.py). PYTHONTRACEMALLOC=25 names where it was
        # opened, at twelve times the run time.
        gc.collect()


def result_value(result):
    with allow_join_result():
        try:
            return result.get(timeout=5)
        finally:
            result.forget()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("rollback", [False, True])
async def test_publish_on_commit_not_rollback(
    queue, rollback, monkeypatch, worker_connections
):
    published, contexts = [], []
    original = Producer.publish

    @async_unsafe("Celery publish on loop")
    def publish(self, *args, **kwargs):
        contexts.append(tenant.get())
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Producer, "publish", publish)

    def save():
        with transaction.atomic():
            transaction.on_commit(lambda: published.append(queue.delay("committed")))
            assert not published
            if rollback:
                transaction.set_rollback(True)

    token = tenant.set("request-tenant")
    try:
        await run_sync(save)()
    finally:
        tenant.reset(token)
    assert len(published) == (0 if rollback else 1)
    assert contexts == ([] if rollback else ["request-tenant"])
    if published:
        result = await run_sync(result_value)(published[0])
        assert result["value"] == "committed"
        assert result["thread"] != threading.get_ident()


async def test_cancelled_publish_waiter_does_not_revoke_enqueued_job(
    queue, monkeypatch
):
    entered, release, finished = (threading.Event() for _ in range(3))
    original = Producer.publish
    published = []

    @async_unsafe("Celery publish on loop")
    def publish(self, *args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Producer, "publish", publish)

    def enqueue():
        try:
            published.append(queue.delay("cancelled-waiter"))
        finally:
            finished.set()

    async with ThreadSensitiveContext():
        pending = asyncio.create_task(run_sync(enqueue)())
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        finally:
            release.set()
            await asyncio.gather(pending, return_exceptions=True)
        assert await asyncio.to_thread(finished.wait, 5)
    assert len(published) == 1
    assert (await run_sync(result_value)(published[0]))["value"] == "cancelled-waiter"


async def test_transport_failure_propagates_without_implicit_retry(queue, monkeypatch):
    attempts = []

    @async_unsafe("Celery publish on loop")
    def fail(self, *args, **kwargs):
        attempts.append(True)
        raise OperationalError("injected broker failure")

    monkeypatch.setattr(Producer, "publish", fail)
    with pytest.raises(OperationalError, match="injected broker failure"):
        await run_sync(queue.apply_async)(args=("failed",), retry=False)
    assert attempts == [True]
