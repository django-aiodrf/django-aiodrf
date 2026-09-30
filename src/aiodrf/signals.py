"""
Signals aiodrf sends.

``asgi_startup`` and ``asgi_shutdown`` are the ASGI lifespan events, sent by
the application of :mod:`aiodrf.asgi` with ``scope``, the lifespan scope.
Receivers may be ``async def``; what a receiver puts in ``scope["state"]``
(where the server provides it) reaches every request as
``request.scope["state"]``.
"""

from django.dispatch import Signal

__all__ = ["asgi_shutdown", "asgi_startup"]

asgi_startup = Signal()
asgi_shutdown = Signal()
