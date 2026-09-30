"""An async command for tests/test_management.py, run by name and in a subprocess."""

import asyncio
import os
import signal
import sys

from asgiref.sync import sync_to_async
from django.core.management.base import CommandError
from django.db import connection

from aiodrf.management import AsyncCommand
from tests.testapp.models import Author


class Command(AsyncCommand):
    help = "Exercise AsyncCommand from the command line."

    def add_arguments(self, parser):
        parser.add_argument(
            "action", choices=["count", "connect", "fail", "exit", "terminate"]
        )

    async def ahandle(self, action, **options):
        match action:
            case "count":
                return str(await Author.objects.acount())
            case "connect":
                await sync_to_async(connection.ensure_connection)()
            case "fail":
                raise CommandError("failed", returncode=3)
            case "exit":
                sys.exit(4)
            case "terminate":
                asyncio.get_running_loop().call_later(
                    0.05, os.kill, os.getpid(), signal.SIGTERM
                )
                try:
                    await asyncio.sleep(10)
                except asyncio.CancelledError:
                    self.stdout.write("cancelled")
                    raise
        return None
