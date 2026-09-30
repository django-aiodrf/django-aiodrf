"""Dual-mode WhiteNoise adapter; file responses retain WhiteNoise's iterator."""

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from typing import cast

from asgiref.sync import sync_to_async
from django.http import HttpRequest, HttpResponseBase
from django.utils.decorators import sync_and_async_middleware

type Response = HttpResponseBase | Awaitable[HttpResponseBase]
type Handler = Callable[[HttpRequest], Response]


async def _discard_response(
    pending: asyncio.Task[HttpResponseBase | None],
) -> None:
    """Close a response opened by a worker after its request was cancelled."""
    # A failed lookup or close (a request_finished receiver) is not raised:
    # cancellation remains the request's outcome.
    try:
        response = await pending
        if response is not None:
            await sync_to_async(response.close, thread_sensitive=False)()
    except Exception:  # noqa: BLE001, S110
        pass


@sync_and_async_middleware
def whitenoise_middleware(get_response: Handler) -> Handler:
    """Keep downstream async views on the loop and file lookup in a worker.

    WhiteNoise still produces a synchronous file iterator. Django buffers
    that iterator under ASGI and warns; use a proxy or an ASGI file server
    when bounded-memory streaming is required.
    """
    from whitenoise.middleware import WhiteNoiseMiddleware

    # Let WhiteNoise own lookup, conditional requests, ranges and headers.
    # Its miss handler is deliberately separate from the Django handler.
    static = WhiteNoiseMiddleware(lambda request: None)
    if not inspect.iscoroutinefunction(get_response):

        def middleware(request: HttpRequest) -> Response:
            response = static(request)
            return response if response is not None else get_response(request)

        return middleware

    lookup = sync_to_async(static, thread_sensitive=False)

    async def async_middleware(request: HttpRequest) -> HttpResponseBase:
        pending = asyncio.create_task(lookup(request))
        try:
            response = await asyncio.shield(pending)
        except asyncio.CancelledError:
            # Cancelling an executor future cannot stop its thread. Drain the
            # lookup and close its response, even after repeated cancellation.
            cleanup = asyncio.create_task(_discard_response(pending))
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    continue
            cleanup.result()
            raise
        if response is not None:
            return response
        return await cast(Awaitable[HttpResponseBase], get_response(request))

    return async_middleware
