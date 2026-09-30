"""
django-typer has no async support: a ``TyperCommand`` whose ``handle`` is
``async def`` returns its coroutine unawaited. Listed first, ``AsyncCommand``
runs that coroutine on its loop: its default ``ahandle`` calls the next
``handle()`` in the MRO, django-typer's, and awaits what it returns.
"""

import asyncio
from io import StringIO

import pytest
from django.core.management import call_command
from django.test import TestCase
from django_typer.management import TyperCommand

from aiodrf.management import AsyncCommand
from tests.testapp.models import Author


class Command(AsyncCommand, TyperCommand):
    async def handle(self, name: str, shout: bool = False):
        count = await Author.objects.acount()
        self.loop = asyncio.get_running_loop()
        self.stdout.write(f"{count} author(s)")
        return f"hello {name}".upper() if shout else f"hello {name}"


# django-typer 4.1 declares its common options (--settings, --traceback, ...)
# with ``shell_complete``, which Typer 0.27 deprecates, when a command is
# first instantiated.
@pytest.mark.filterwarnings(
    "ignore:In Typer, only the parameter 'autocompletion':DeprecationWarning"
)
class TyperCommandTests(TestCase):
    def setUp(self):
        Author.objects.create(name="Ursula")

    def test_an_async_typer_handle_runs_on_the_commands_loop(self):
        command = Command()
        out = StringIO()
        # Typer parsed the arguments; django-typer prints a returned value
        # only when asked to (DT_PRINT_RESULT), for synchronous commands too.
        assert call_command(command, "world", "--shout", stdout=out) == "HELLO WORLD"
        assert out.getvalue() == "1 author(s)\n"
        assert command.loop is not None
