"""Shared test-client adapters and version-aware serializer assertions."""

from asgiref.sync import sync_to_async

from aiodrf.test import APIClient, AsyncAPIClient


class AsyncTransport:
    """Exercise views through Django's async test handler, without ASGI lifespan."""

    transport = "asgi"

    def setUp(self):
        super().setUp()
        self.client = AsyncAPIClient()

    async def api(self, method, path, **kwargs):
        return await getattr(self.client, method)(path, **kwargs)


class SyncTransport:
    """Exercise views through Django's sync test handler and async-view adapter."""

    transport = "wsgi"

    def setUp(self):
        super().setUp()
        self.client = APIClient()

    async def api(self, method, path, **kwargs):
        return await sync_to_async(getattr(self.client, method))(path, **kwargs)


def both_transports(cls):
    """Create ``<Name>ASGI`` and ``<Name>WSGI`` test cases from a mixin."""
    import sys

    from django.test import TestCase

    module = sys.modules[cls.__module__]
    for transport in (AsyncTransport, SyncTransport):
        name = f"{cls.__name__.removeprefix('_')}{transport.transport.upper()}"
        setattr(
            module,
            name,
            type(name, (transport, cls, TestCase), {"__module__": cls.__module__}),
        )
    return cls


def list_errors(errors, length):
    """
    Expected ``ListSerializer`` errors: DRF 3.18 keys them by index when
    ``LIST_SERIALIZER_ERRORS_AS_DICT`` is on; earlier versions pad a list.
    """
    from rest_framework.settings import api_settings

    if getattr(api_settings, "LIST_SERIALIZER_ERRORS_AS_DICT", False):
        return errors
    return [errors.get(index, {}) for index in range(length)]
