"""
Cancelling a native write while Django's transaction around it, on Django's
connection, is being entered, used or ended (``receivers_in_transaction``,
``nox -s native_db``).

Cancelling a task stops its wait for the sync thread, not the thread: what
the thread entered must still be exited there, once, and the task must wait
for it before the cancellation goes on.
"""

import asyncio
import threading
from types import SimpleNamespace

import pytest
from django.db import connection, transaction

from aiodrf.contrib.async_backend import _package
from aiodrf.utils import run_sync


class Paused:
    """
    ``inner``, whose ``__enter__`` or ``__exit__`` waits in the sync thread
    until released, after its work (``after=True``) or before it.
    """

    def __init__(self, inner, pause, after=True):
        self.inner, self.pause, self.after = inner, pause, after
        self.calls = []
        self.reached = threading.Event()
        self.release = threading.Event()

    def _wait(self, step):
        if step == self.pause:
            self.reached.set()
            assert self.release.wait(5)

    def __enter__(self):
        if not self.after:
            self._wait("enter")
        self.inner.__enter__()
        self.calls.append("enter")
        if self.after:
            self._wait("enter")

    def __exit__(self, exc_type, exc, tb):
        if not self.after:
            self._wait("exit")
        self.calls.append(("exit", exc_type))
        result = self.inner.__exit__(exc_type, exc, tb)
        if self.after:
            self._wait("exit")
        return result


class Plain:
    def __init__(self, fail=False):
        self.fail = fail

    def __enter__(self):
        if self.fail:
            raise ValueError("entry failed")

    def __exit__(self, *exc_info):
        return None


@pytest.fixture
def pause(monkeypatch):
    """Make the helper open ``Paused(inner)``, ``transaction.atomic()`` by default."""

    def install(step, after=True, inner=None):
        block = Paused(inner or transaction.atomic(using="default"), step, after)
        monkeypatch.setattr(
            _package, "transaction", SimpleNamespace(atomic=lambda using: block)
        )
        return block

    return install


@pytest.fixture
def stubbed(monkeypatch):
    """A native connection outside a transaction, and a receiver, without the real ones."""
    monkeypatch.setattr(
        _package,
        "async_connections",
        {"default": SimpleNamespace(in_atomic_block=False)},
    )
    monkeypatch.setattr(
        _package, "_WRITE_SIGNALS", [SimpleNamespace(receivers=[object()])]
    )


async def use(body=None):
    async with _package.receivers_in_transaction("default"):
        if body is not None:
            await body()


async def cancel_while_paused(block, task, times=1):
    assert await asyncio.to_thread(block.reached.wait, 5)
    for _ in range(times):
        task.cancel()
        await asyncio.sleep(0.01)
    # The task waits for the thread, whatever the number of cancellations.
    assert not task.done()
    block.release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)


def sync_state():
    """Django's connection in the sync thread: atomic, savepoints, session status."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM pg_stat_activity"
            " WHERE datname = current_database() AND state LIKE 'idle in transaction%%'"
        )
        (idle_in_transaction,) = cursor.fetchone()
    return connection.in_atomic_block, connection.savepoint_ids[:], idle_in_transaction


# -- the helper's contract ----------------------------------------------------------------


@pytest.mark.parametrize("times", [1, 3])
@pytest.mark.parametrize("after", [True, False], ids=["entered", "entering"])
async def test_cancelled_while_entering_exits_what_was_entered_once(
    stubbed, pause, after, times
):
    block = pause("enter", after=after, inner=Plain())
    task = asyncio.create_task(use())
    await cancel_while_paused(block, task, times)
    assert block.calls == ["enter", ("exit", asyncio.CancelledError)]


async def test_a_failed_entry_is_not_exited(stubbed, pause):
    block = pause("none", inner=Plain(fail=True))
    with pytest.raises(ValueError, match="entry failed"):
        await use()
    assert block.calls == []


async def test_cancelled_in_the_body_exits_once_with_the_cancellation(stubbed, pause):
    block = pause("none", inner=Plain())
    started = asyncio.Event()

    async def body():
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(use(body))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert block.calls == ["enter", ("exit", asyncio.CancelledError)]


@pytest.mark.parametrize("times", [1, 3])
@pytest.mark.parametrize("failing", [False, True], ids=["commit", "rollback"])
async def test_cancelled_while_exiting_waits_for_the_one_exit(
    stubbed, pause, failing, times
):
    block = pause("exit", after=False, inner=Plain())

    async def body():
        if failing:
            raise ValueError("body failed")

    task = asyncio.create_task(use(body))
    await cancel_while_paused(block, task, times)
    # A commit stays a commit: the cancellation came after it started.
    assert block.calls == ["enter", ("exit", ValueError if failing else None)]


# -- Django's transaction, on PostgreSQL -----------------------------------------------


def test_the_project_has_write_signal_receivers():
    # The helper does nothing without them; django-cleanup and cacheops connect some.
    assert any(signal.receivers for signal in _package._WRITE_SIGNALS)


@pytest.mark.parametrize("after", [True, False], ids=["entered", "entering"])
async def test_cancelled_entry_leaves_djangos_connection_outside_a_transaction(
    pause, after
):
    block = pause("enter", after=after)
    task = asyncio.create_task(use())
    await cancel_while_paused(block, task)
    assert await run_sync(sync_state)() == (False, [], 0)
    assert block.calls == ["enter", ("exit", asyncio.CancelledError)]


async def test_cancelled_entry_of_a_nested_transaction_leaves_the_outer_one(pause):
    outer = transaction.atomic(using="default")
    await run_sync(outer.__enter__)()
    try:
        block = pause("enter")
        task = asyncio.create_task(use())
        await cancel_while_paused(block, task)
        in_atomic, savepoints, _ = await run_sync(sync_state)()
        assert (in_atomic, savepoints) == (True, [])
    finally:
        await run_sync(outer.__exit__)(None, None, None)
    assert await run_sync(sync_state)() == (False, [], 0)


@pytest.mark.parametrize("failing", [False, True], ids=["commit", "rollback"])
async def test_cancelled_while_ending_leaves_djangos_connection_outside_a_transaction(
    pause, failing
):
    block = pause("exit", after=False)

    async def body():
        # Inside the native transaction, as aiodrf's native writes are.
        try:
            async with _package.async_atomic(using="default"):
                if failing:
                    raise ValueError("body failed")
        finally:
            # This task's native connection, which the test's cannot close.
            await _package.async_connections["default"].close()

    task = asyncio.create_task(use(body))
    await cancel_while_paused(block, task)
    assert await run_sync(sync_state)() == (False, [], 0)
    assert not _package.async_connections["default"].in_atomic_block
    assert block.calls == ["enter", ("exit", ValueError if failing else None)]
