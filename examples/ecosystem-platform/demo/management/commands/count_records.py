"""A typed management command using aiodrf's async command boundary."""

from django_typer.management import TyperCommand

from aiodrf.management import AsyncCommand
from demo.models import Record


class Command(AsyncCommand, TyperCommand):
    async def handle(self, prefix: str = "Records") -> str:
        return f"{prefix}: {await Record.objects.acount()}"
