"""Wireup owns callback wrapping; aiodrf's dispatch remains asynchronous."""

from wireup import Injected

from aiodrf.response import Response
from aiodrf.views import APIView

from .services import Greeter


class Greeting(APIView):
    def __init__(self, greeter: Injected[Greeter], **kwargs):
        super().__init__(**kwargs)
        self.greeter = greeter

    async def get(self, request):
        return Response({"greeting": self.greeter.greet("reader")})
