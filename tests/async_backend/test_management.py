"""AsyncCommand with django-async-backend: native connections end with the command."""

import gc
from io import StringIO

from django.core.management import call_command

from aiodrf.management import AsyncCommand
from tests.testapp.models import Author


class NativeCount(AsyncCommand):
    async def ahandle(self, *args, **options):
        from django_async_backend.db import async_connections

        count = await Author.async_objects.acount()
        self.native = async_connections["default"]
        assert self.native.connection is not None
        return str(count)


def test_native_connections_are_closed_on_the_commands_loop(transactional_db):
    Author.objects.create(name="Ursula")
    command = NativeCount()
    out = StringIO()
    call_command(command, stdout=out)
    assert out.getvalue() == "1\n"
    # Closed before the loop ended: a connection left open would be
    # finalized with a ResourceWarning, or closed on a closed loop.
    assert command.native.connection is None
    del command
    gc.collect()
