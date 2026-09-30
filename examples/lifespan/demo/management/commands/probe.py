"""Management command exercising typed lifespan resource access."""

from aiodrf.management import AsyncCommand
from demo.lifecycle import lifespan


class Command(AsyncCommand):
    help = "Use the same resource factory in a command-owned lifetime."

    async def ahandle(self, *args, **options):
        async with lifespan() as resources:
            response = await resources.http.get("/value")
            self.stdout.write(str(response.json()["value"]))
