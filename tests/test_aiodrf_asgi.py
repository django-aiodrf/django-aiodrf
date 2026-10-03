"""aiodrf's ASGI application answers the lifespan protocol, as in 0.0.2."""

import asyncio
import sys

import pytest
from aiodrf_asgi_lifespan import signals

from aiodrf.asgi import get_asgi_application


async def _lifespan(application):
    incoming = asyncio.Queue()
    for phase in ("startup", "shutdown"):
        incoming.put_nowait({"type": f"lifespan.{phase}"})
    sent = []

    async def send(message):
        sent.append(message["type"])

    scope = {"type": "lifespan", "asgi": {"version": "3.0"}, "state": {}}
    await asyncio.wait_for(application(scope, incoming.get, send), 5)
    return sent


async def test_without_a_lifespan_feature_the_protocol_is_answered():
    calls = []

    async def received(sender, scope, **kwargs):
        calls.append(scope["type"])

    signals.asgi_startup.connect(received)
    signals.asgi_shutdown.connect(received)
    try:
        sent = await _lifespan(get_asgi_application())
    finally:
        signals.asgi_startup.disconnect(received)
        signals.asgi_shutdown.disconnect(received)
    assert sent == ["lifespan.startup.complete", "lifespan.shutdown.complete"]
    # With the lifespan package installed, its signals are sent.
    assert calls == ["lifespan", "lifespan"]


async def test_without_the_lifespan_package_the_protocol_is_answered(monkeypatch):
    monkeypatch.setitem(sys.modules, "aiodrf_asgi_lifespan", None)
    sent = await _lifespan(get_asgi_application())
    assert sent == ["lifespan.startup.complete", "lifespan.shutdown.complete"]


def test_a_lifespan_feature_without_the_package_is_a_configuration_error(
    monkeypatch, settings
):
    from django.core.exceptions import ImproperlyConfigured

    monkeypatch.setitem(sys.modules, "aiodrf_asgi_lifespan", None)
    settings.DJANGO_LIFESPAN = "contextlib.nullcontext"
    with pytest.raises(ImproperlyConfigured, match="lifespan"):
        get_asgi_application()
