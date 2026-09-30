"""All five calls must fail type checking; Any must not make this pass."""

from django.http import HttpRequest

from aiodrf.asgi import LifespanApplication, get_lifespan_state
from aiodrf.response import StreamingResponse
from aiodrf.serializers import Serializer


async def invalid_calls() -> None:
    await Serializer(data={}).ais_valid(raise_exception="true")
    StreamingResponse([], chunk_size="10")


async def application(*args: object) -> None:
    pass


def not_a_context() -> None:
    pass


def needs_integer(value: int) -> None:
    pass


def invalid_lifespan(http_request: HttpRequest) -> None:
    LifespanApplication(application, lifespan=not_a_context)
    get_lifespan_state(http_request, "str")
    needs_integer(get_lifespan_state(http_request, str))
