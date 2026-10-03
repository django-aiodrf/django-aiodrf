"""aiodrf.management: async commands on Django's command machinery."""

import asyncio
import os
import signal
import subprocess
import sys
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from django.core.exceptions import ImproperlyConfigured, SynchronousOnlyOperation
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, connections
from django.test import TestCase, override_settings

from aiodrf.management import AsyncCommand, _Cancellation, acall_command
from tests.testapp.models import Author

ROOT = Path(__file__).resolve().parent.parent


def run(command, *args, **options):
    out = StringIO()
    call_command(command, *args, stdout=out, **options)
    return out.getvalue()


def running_loop():
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


# -- Writing a command ------------------------------------------------------------


class Echo(AsyncCommand):
    def add_arguments(self, parser):
        parser.add_argument("word")

    async def ahandle(self, word, **options):
        self.stdout.write("written")
        await asyncio.sleep(0)
        return f"{word}!"


class AsyncHandle(AsyncCommand):
    async def handle(self, *args, **options):
        await asyncio.sleep(0)
        return "from handle"


class AsyncHandleMixin:
    async def handle(self, *args, **options):
        return "from a mixin"


class MixedIn(AsyncHandleMixin, AsyncCommand):
    pass


class Transactional(AsyncCommand):
    output_transaction = True

    async def ahandle(self, *args, **options):
        return "SELECT 1;"


def test_writes_and_the_returned_string_reach_stdout():
    assert run(Echo(), "hi") == "written\nhi!\n"


@pytest.mark.parametrize(
    ("command", "output"), [(AsyncHandle, "from handle\n"), (MixedIn, "from a mixin\n")]
)
def test_an_async_handle_is_awaited(command, output):
    # Django's execute() calls handle(): a coroutine function there must
    # reach the event loop, not be written out (or dropped) unawaited.
    assert run(command()) == output


def test_output_transaction_wraps_the_returned_sql():
    assert run(Transactional()).splitlines() == ["BEGIN;", "SELECT 1;", "COMMIT;"]


def test_a_command_without_a_handler_is_djangos_error():
    with pytest.raises(NotImplementedError, match="handle"):
        run(AsyncCommand())


def test_a_command_can_run_from_a_worker_thread():
    # Only the main thread can install signal handlers; elsewhere none are.
    result = []
    worker = threading.Thread(target=lambda: result.append(run(Echo(), "thread")))
    worker.start()
    worker.join()
    assert result == ["written\nthread!\n"]


class Checked(AsyncCommand):
    requires_system_checks = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.calls = []

    def check(self, *args, **kwargs):
        self.calls.append(("check", threading.current_thread(), running_loop()))
        return super().check(*args, **kwargs)

    async def ahandle(self, *args, **options):
        self.calls.append(("ahandle", threading.current_thread(), running_loop()))


@pytest.mark.django_db(databases="__all__")
def test_system_checks_run_synchronously_before_the_loop_starts():
    command = Checked()
    run(command, skip_checks=False)
    assert [name for name, *_ in command.calls] == ["check", "ahandle"]
    assert command.calls[0][1:] == (threading.current_thread(), None)
    assert command.calls[1][2] is not None


def test_skip_checks_is_honoured(db):
    command = Checked()
    run(command)
    assert [name for name, *_ in command.calls] == ["ahandle"]


# -- Database connections ---------------------------------------------------------


class Count(AsyncCommand):
    async def ahandle(self, *args, **options):
        return str(await Author.objects.acount())


class Touch(AsyncCommand):
    async def ahandle(self, *args, **options):
        def touch():
            connection.ensure_connection()
            return threading.current_thread(), connections["default"]

        self.touched = await sync_to_async(touch)()


def test_thread_sensitive_work_uses_the_callers_connection(db):
    # The connection Django's run_from_argv() closes (connections.close_all()
    # in the calling thread) is the one the async ORM used.
    command = Touch()
    run(command)
    assert command.touched == (threading.current_thread(), connections["default"])


class CommandTransactionTests(TestCase):
    def setUp(self):
        Author.objects.create(name="Ursula")

    def test_the_async_orm_sees_the_test_transaction(self):
        assert run(Count()) == "1\n"

    async def test_acall_command_sees_the_test_transaction(self):
        out = StringIO()
        await acall_command("async_probe", "count", stdout=out)
        assert out.getvalue() == "1\n"
        assert await acall_command(Count()) == "1"


async def test_call_command_inside_a_running_loop_names_acall_command():
    with pytest.raises(SynchronousOnlyOperation, match=r"acall_command"):
        call_command(Echo(), "loop")


# -- Signals ----------------------------------------------------------------------


class Interrupted(AsyncCommand):
    signum = signal.SIGINT

    async def ahandle(self, *args, **options):
        asyncio.get_running_loop().call_later(0.05, os.kill, os.getpid(), self.signum)
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            # A second signal gets the handler that was there before.
            self.handler_during_cleanup = signal.getsignal(self.signum)
            self.cleanup_thread = await sync_to_async(threading.current_thread)()
            raise
        return "not interrupted"


def test_sigint_cancels_the_task_and_raises_keyboard_interrupt():
    previous = signal.getsignal(signal.SIGINT)
    command = Interrupted()
    started = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        run(command)
    assert time.monotonic() - started < 5
    assert command.cleanup_thread is threading.current_thread()
    assert command.handler_during_cleanup is previous
    assert signal.getsignal(signal.SIGINT) is previous


class FailsWhileCancelled(Interrupted):
    async def ahandle(self, *args, **options):
        asyncio.get_running_loop().call_later(0.05, os.kill, os.getpid(), self.signum)
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            raise ConnectionError("cleanup failed") from None
        return "not interrupted"


def test_a_signal_wins_over_an_error_raised_while_cancelling():
    with pytest.raises(KeyboardInterrupt) as raised:
        run(FailsWhileCancelled())
    # The cleanup's error is not lost: it is the interrupt's context.
    assert isinstance(raised.value.__context__, ConnectionError)


def test_a_projects_own_sigint_handler_is_left_alone():
    received = []

    class Handled(Interrupted):
        async def ahandle(self, *args, **options):
            asyncio.get_running_loop().call_later(
                0.05, os.kill, os.getpid(), signal.SIGINT
            )
            await asyncio.sleep(0.3)
            return "finished"

    def handler(signum, frame):
        received.append(signum)

    previous = signal.signal(signal.SIGINT, handler)
    try:
        assert run(Handled()) == "finished\n"
        assert signal.getsignal(signal.SIGINT) is handler
    finally:
        signal.signal(signal.SIGINT, previous)
    assert received == [signal.SIGINT]


def test_a_signal_after_the_loop_closed_is_kept_for_the_command():
    # The window between the handler's end and __exit__ restoring the
    # handlers: the loop is closed, and nothing is left to cancel.
    cancellation = _Cancellation()

    async def handler():
        await cancellation.started()

    async_to_sync(handler)()
    cancellation.cancel(signal.SIGINT, None)
    with pytest.raises(KeyboardInterrupt):
        cancellation.reraise()


class InterruptedAtTheEnd(AsyncCommand):
    async def ahandle(self, *args, **options):
        # No await follows: the handler returns whether or not the signal
        # reached it first.
        os.kill(os.getpid(), signal.SIGINT)
        return "finished"


def test_a_signal_as_the_handler_returns_still_interrupts():
    previous = signal.getsignal(signal.SIGINT)
    with pytest.raises(KeyboardInterrupt):
        run(InterruptedAtTheEnd())
    assert signal.getsignal(signal.SIGINT) is previous


# -- From the command line --------------------------------------------------------


def manage(tmp_path, *argv):
    """``manage.py <argv>`` in a fresh interpreter, on an SQLite file."""
    (tmp_path / "command_settings.py").write_text(
        "from tests.settings import *  # noqa: F403\n"
        f"DATABASES = {{'default': {{'ENGINE': 'django.db.backends.sqlite3', "
        f"'NAME': {str(tmp_path / 'db.sqlite3')!r}}}}}\n"
    )
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(tmp_path), str(ROOT / "src"), str(ROOT)]),
        "DJANGO_SETTINGS_MODULE": "command_settings",
    }
    script = (
        "import sys\n"
        "from django.core.management import execute_from_command_line\n"
        "execute_from_command_line(['manage.py', *sys.argv[1:]])"
    )
    return subprocess.run(
        [
            sys.executable,
            "-W",
            "error",
            "-c",
            script,
            *argv,
        ],
        env=env,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def test_the_commands_connections_are_closed(tmp_path):
    result = manage(tmp_path, "async_probe", "connect")
    assert result.returncode == 0, result.stderr
    # A connection opened in another thread would be finalized open.
    assert "ResourceWarning" not in result.stderr


@pytest.mark.parametrize(
    ("action", "status", "stderr"),
    [("fail", 3, "CommandError: failed"), ("exit", 4, "")],
)
def test_exit_statuses(tmp_path, action, status, stderr):
    result = manage(tmp_path, "async_probe", action)
    assert result.returncode == status, result.stderr
    assert stderr in result.stderr


def test_sigterm_cancels_the_task_and_exits_with_143(tmp_path):
    result = manage(tmp_path, "async_probe", "terminate")
    assert result.returncode == 128 + signal.SIGTERM, result.stderr
    assert result.stdout == "cancelled\n"


# -- Lifespan ---------------------------------------------------------------------


@dataclass
class Resource:
    loop: asyncio.AbstractEventLoop
    events: list = field(default_factory=list)


RESOURCES = []


@asynccontextmanager
async def lifespan():
    resource = Resource(asyncio.get_running_loop())
    RESOURCES.append(resource)
    resource.events.append("enter")
    try:
        yield resource
    except BaseException as exc:
        resource.events.append(type(exc))
        raise
    finally:
        resource.events.append("exit")


@asynccontextmanager
async def failing_lifespan():
    raise ConnectionError("unreachable")
    yield


class WithLifespan(AsyncCommand):
    lifespan = True

    async def ahandle(self, *args, **options):
        resource = self.get_lifespan_state(Resource)
        assert resource.loop is asyncio.get_running_loop()
        assert resource.events == ["enter"]
        return "used"


class FailsWithLifespan(WithLifespan):
    async def ahandle(self, *args, **options):
        raise CommandError("broken")


class WithoutLifespan(AsyncCommand):
    async def ahandle(self, *args, **options):
        return self.get_lifespan_state(Resource)


@pytest.fixture
def configured():
    RESOURCES.clear()
    with override_settings(AIODRF={}, DJANGO_LIFESPAN=lifespan, FASTDRF={}):
        yield RESOURCES
    RESOURCES.clear()


def test_the_lifespan_is_entered_once_on_the_commands_loop(configured):
    command = WithLifespan()
    assert run(command) == "used\n"
    (resource,) = configured
    assert resource.events == ["enter", "exit"]
    with pytest.raises(ImproperlyConfigured, match="No active"):
        command.get_lifespan_state(Resource)


def test_the_lifespan_is_opt_in(configured):
    with pytest.raises(ImproperlyConfigured, match="No active"):
        run(WithoutLifespan())
    assert configured == []


def test_the_lifespan_state_is_typed(configured):
    class WrongType(WithLifespan):
        async def ahandle(self, *args, **options):
            return self.get_lifespan_state(dict)

    with pytest.raises(
        ImproperlyConfigured, match=r"Lifespan state must be dict, not Resource\."
    ):
        run(WrongType())


def test_the_handlers_exception_reaches_the_lifespan(configured):
    with pytest.raises(CommandError, match="broken"):
        run(FailsWithLifespan())
    (resource,) = configured
    assert resource.events == ["enter", CommandError, "exit"]


def test_a_failing_lifespan_propagates():
    with (
        override_settings(AIODRF={}, DJANGO_LIFESPAN=failing_lifespan, FASTDRF={}),
        pytest.raises(ConnectionError, match="unreachable"),
    ):
        run(WithLifespan())


def test_a_lifespan_command_needs_the_setting():
    with pytest.raises(ImproperlyConfigured, match="LIFESPAN"):
        run(WithLifespan())
