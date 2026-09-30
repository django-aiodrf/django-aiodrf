"""Enqueue placement, commit ordering and failure are the backend's contract."""

import asyncio
import contextvars
import threading

import pytest
from asgiref.sync import ThreadSensitiveContext
from django.db import transaction
from django.test import override_settings
from django.utils.asyncio import async_unsafe

from aiodrf.utils import run_sync

tasks = pytest.importorskip("django.tasks", reason="Django 6.0 added tasks")
from django.tasks.backends.dummy import DummyBackend  # noqa: E402

tenant = contextvars.ContextVar("test_task_tenant", default=None)


class RecordingBackend(DummyBackend):
    @async_unsafe("enqueue on loop")
    def enqueue(self, task, args, kwargs):
        if kwargs.get("fail"):
            raise ConnectionError("queue unavailable")
        result = super().enqueue(task, args, kwargs)
        return result, tenant.get(), threading.get_ident()


@tasks.task
def task_contract_job(value, *, fail=False):
    raise AssertionError("Enqueue must not run the job")


backend_settings = override_settings(
    TASKS={"default": {"BACKEND": f"{__name__}.RecordingBackend"}}
)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("rollback", [False, True])
async def test_enqueue_after_commit_does_not_run_on_rollback(
    rollback, worker_connections
):
    observed = []

    def save():
        with transaction.atomic():
            transaction.on_commit(
                lambda: observed.append(task_contract_job.enqueue("later"))
            )
            if rollback:
                transaction.set_rollback(True)

    with backend_settings:
        await run_sync(save)()
    assert len(observed) == (0 if rollback else 1)


async def test_backend_enqueue_failure_and_context_isolation():
    async def enqueue(value):
        token = tenant.set(value)
        try:
            _result, recorded, thread = await task_contract_job.aenqueue(value)
            assert recorded == value
            assert thread != threading.get_ident()
        finally:
            tenant.reset(token)

    with backend_settings:
        await asyncio.gather(*(enqueue(value) for value in range(8)))
        with pytest.raises(ConnectionError, match="queue unavailable"):
            await task_contract_job.aenqueue("failed", fail=True)
    assert tenant.get() is None


async def test_cancelled_enqueue_waiter_does_not_undo_running_backend(monkeypatch):
    entered, release, finished = (threading.Event() for _ in range(3))
    original = RecordingBackend.enqueue
    results = []

    @async_unsafe("enqueue on loop")
    def enqueue(self, task, args, kwargs):
        entered.set()
        assert release.wait(3)
        try:
            results.append(original(self, task, args, kwargs))
            return results[-1]
        finally:
            finished.set()

    monkeypatch.setattr(RecordingBackend, "enqueue", enqueue)
    with backend_settings:
        async with ThreadSensitiveContext():
            pending = asyncio.create_task(task_contract_job.aenqueue("cancelled"))
            try:
                assert await asyncio.to_thread(entered.wait, 3)
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
            finally:
                release.set()
                await asyncio.gather(pending, return_exceptions=True)
            assert await asyncio.to_thread(finished.wait, 3)
    assert len(results) == 1
