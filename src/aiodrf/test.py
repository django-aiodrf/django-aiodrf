"""
Test utilities for async views.

``AsyncAPIClient`` and ``AsyncAPIRequestFactory`` are Django's async test
client and request factory with DRF's request encoding (``format="json"``,
``TEST_REQUEST_RENDERER_CLASSES``), ``credentials()`` and
``force_authenticate()``. DRF's synchronous ``APIClient`` keeps working
against aiodrf views as well: Django runs async views through
``async_to_sync``.
"""

import asyncio
import contextlib
from collections.abc import AsyncGenerator
from typing import Any

from django.http import HttpResponseBase
from django.test.client import AsyncClient, AsyncClientHandler, AsyncRequestFactory
from rest_framework.settings import api_settings
from rest_framework.test import (  # noqa: F401
    APIClient,
    APIRequestFactory,
    APITestCase,
    APITransactionTestCase,
    force_authenticate,
)

from aiodrf.asgi import LifespanApplication, LifespanFactory
from aiodrf.settings import aiodrf_settings
from aiodrf.utils import count_hops

__all__ = [
    "AsyncAPIClient",
    "AsyncAPIRequestFactory",
    "AsyncForceAuthClientHandler",
    "count_hops",
    "force_authenticate",
    "lifespan",
]

_UNSET: Any = object()


async def _no_http(scope: Any, receive: Any, send: Any) -> None:
    raise AssertionError("The test lifespan answers lifespan scopes only.")


@contextlib.asynccontextmanager
async def lifespan(
    factory: LifespanFactory[object] | None = _UNSET,
) -> AsyncGenerator[dict[str, Any]]:
    """
    Run the application's lifespan around a block, as an ASGI server would:
    enter ``factory`` (``AIODRF["LIFESPAN"]`` by default), send the startup
    signal, and on exit the shutdown signal, then close. Yields the lifespan
    state the server would keep; give it to ``AsyncAPIClient(lifespan=...)``
    or ``AsyncAPIRequestFactory(lifespan=...)``. A failed startup or
    shutdown raises ``RuntimeError`` with the application's report.
    """
    if factory is _UNSET:
        factory = aiodrf_settings.LIFESPAN
    application = LifespanApplication(_no_http, lifespan=factory)
    state: dict[str, Any] = {}
    scope = {
        "type": "lifespan",
        "asgi": {"version": "3.0", "spec_version": "2.0"},
        "state": state,
    }
    incoming: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    outgoing: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    task = asyncio.create_task(application(scope, incoming.get, outgoing.put))  # type: ignore[arg-type]

    async def run(phase: Any) -> None:
        await incoming.put({"type": f"lifespan.{phase}"})
        message = await outgoing.get()
        if message["type"] == f"lifespan.{phase}.failed":
            await task
            raise RuntimeError(f"Lifespan {phase} failed:\n{message['message']}")

    try:
        await run("startup")
    except BaseException:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        raise
    try:
        yield state
    finally:
        await run("shutdown")
        await task


def _asgi_headers(extra: Any) -> dict[str, Any]:
    """
    Translate WSGI-style keys (``HTTP_AUTHORIZATION``), which DRF's test
    client accepts, to the header names Django's async client expects.
    """
    return {(key.removeprefix("HTTP_")): value for key, value in extra.items()}


class _APIEncodingMixin:
    defaults: Any
    renderer_classes_list = api_settings.TEST_REQUEST_RENDERER_CLASSES
    default_format = api_settings.TEST_REQUEST_DEFAULT_FORMAT

    def __init__(
        self,
        *args: Any,
        enforce_csrf_checks: bool = False,
        lifespan: Any = None,
        **defaults: Any,
    ) -> None:
        self.enforce_csrf_checks = enforce_csrf_checks
        #: The state of :func:`lifespan`, which every request gets a copy of.
        self.lifespan_state = lifespan
        self.renderer_classes = {cls.format: cls for cls in self.renderer_classes_list}
        super().__init__(*args, **defaults)
        # Django's async factory sends each default as a header by its key,
        # so ``HTTP_AUTHORIZATION`` (DRF's ``APIClient(HTTP_AUTHORIZATION=...)``,
        # or ``headers=`` which Django stores under WSGI names) would arrive
        # as ``Http-Authorization``.
        self.defaults = _asgi_headers(self.defaults)

    def _base_scope(self, **request: Any) -> dict[str, Any]:
        scope = super()._base_scope(**request)  # type: ignore[misc]
        if self.lifespan_state is not None:
            # As an ASGI server does: a shallow copy per request.
            scope["state"] = dict(self.lifespan_state)
        return scope

    # DRF's ``_encode_data``, which Django's ``_encode_data`` would shadow.
    _encode_api_data = APIRequestFactory._encode_data  # type: ignore[attr-defined]

    def _api_request(
        self,
        method: str,
        path: str,
        data: Any,
        format: str | None,
        content_type: str | None,
        **extra: Any,
    ) -> Any:
        data, content_type = self._encode_api_data(data, format, content_type)
        # ``generic`` is the request factory's this mixin is combined with.
        return self.generic(method, path, data, content_type, **_asgi_headers(extra))  # type: ignore[attr-defined]


class AsyncAPIRequestFactory(_APIEncodingMixin, AsyncRequestFactory):
    def generic(
        self,
        method: str,
        path: str,
        data: Any = "",
        content_type: str | None = "application/octet-stream",
        **extra: Any,
    ) -> Any:
        return super().generic(method, path, data, content_type, **_asgi_headers(extra))

    def get(self, path: str, data: Any = None, **extra: Any) -> Any:
        return super().get(path, data=data, **_asgi_headers(extra))

    def head(self, path: str, data: Any = None, **extra: Any) -> Any:
        return super().head(path, data=data, **_asgi_headers(extra))

    def trace(self, path: str, **extra: Any) -> Any:
        return super().trace(path, **_asgi_headers(extra))

    def post(
        self,
        path: str,
        data: Any = None,
        format: str | None = None,
        content_type: str | None = None,
        **extra: Any,
    ) -> Any:
        return self._api_request("POST", path, data, format, content_type, **extra)

    def put(
        self,
        path: str,
        data: Any = None,
        format: str | None = None,
        content_type: str | None = None,
        **extra: Any,
    ) -> Any:
        return self._api_request("PUT", path, data, format, content_type, **extra)

    def patch(
        self,
        path: str,
        data: Any = None,
        format: str | None = None,
        content_type: str | None = None,
        **extra: Any,
    ) -> Any:
        return self._api_request("PATCH", path, data, format, content_type, **extra)

    def delete(
        self,
        path: str,
        data: Any = None,
        format: str | None = None,
        content_type: str | None = None,
        **extra: Any,
    ) -> Any:
        return self._api_request("DELETE", path, data, format, content_type, **extra)

    def options(
        self,
        path: str,
        data: Any = None,
        format: str | None = None,
        content_type: str | None = None,
        **extra: Any,
    ) -> Any:
        return self._api_request("OPTIONS", path, data, format, content_type, **extra)

    def query(
        self,
        path: str,
        data: Any = None,
        format: str | None = None,
        content_type: str | None = None,
        **extra: Any,
    ) -> Any:
        """A QUERY request (RFC 10008), encoded like ``post()``."""
        return self._api_request("QUERY", path, data, format, content_type, **extra)

    def request(self, **kwargs: Any) -> Any:
        request = super().request(**kwargs)
        # Read by Django's CSRF middleware; not in Django's stubs.
        request._dont_enforce_csrf_checks = not self.enforce_csrf_checks  # type: ignore[attr-defined]
        return request


class AsyncForceAuthClientHandler(AsyncClientHandler):
    """``AsyncClientHandler`` that can enforce authentication on requests."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._force_user = None
        self._force_token = None
        super().__init__(*args, **kwargs)

    async def get_response_async(self, request: Any) -> HttpResponseBase:
        # This is the simplest place we can hook into to patch the
        # request object.
        force_authenticate(request, self._force_user, self._force_token)
        return await super().get_response_async(request)


class AsyncAPIClient(_APIEncodingMixin, AsyncClient):
    handler: AsyncForceAuthClientHandler

    def __init__(self, enforce_csrf_checks: bool = False, **defaults: Any) -> None:
        super().__init__(enforce_csrf_checks=enforce_csrf_checks, **defaults)
        self.handler = AsyncForceAuthClientHandler(enforce_csrf_checks)
        self._credentials: dict[str, Any] = {}

    def credentials(self, **kwargs: Any) -> None:
        """
        Sets headers that will be used on every outgoing request.
        """
        self._credentials = _asgi_headers(kwargs)

    def force_authenticate(self, user: Any = None, token: Any = None) -> None:
        """
        Forcibly authenticates outgoing requests with the given
        user and/or token. Unlike DRF's client, clearing it does not log out
        a session; use ``await client.alogout()`` for that.
        """
        self.handler._force_user = user
        self.handler._force_token = token

    def logout(self) -> None:
        """As DRF's client: clears ``credentials()`` and ``force_authenticate()``."""
        self._forget_authentication()
        super().logout()

    async def alogout(self) -> None:
        """As :meth:`logout`."""
        self._forget_authentication()
        await super().alogout()

    def _forget_authentication(self) -> None:
        self._credentials = {}
        self.handler._force_user = None
        self.handler._force_token = None

    def generic(
        self,
        method: str,
        path: str,
        data: Any = "",
        content_type: str | None = "application/octet-stream",
        **extra: Any,
    ) -> Any:
        # A request's own headers take precedence over ``credentials()``.
        return super().generic(
            method,
            path,
            data,
            content_type,
            **{**self._credentials, **_asgi_headers(extra)},
        )

    # ``format`` reaches these from Django's redirect handling (``follow``),
    # which passes the encoded request's arguments on. DRF's methods have no
    # such parameter either, and in Django's async client it would be a
    # header.

    async def get(
        self,
        path: str,
        data: Any = None,
        follow: bool = False,
        format: str | None = None,
        **extra: Any,
    ) -> Any:
        return await super().get(path, data=data, follow=follow, **_asgi_headers(extra))

    async def head(
        self,
        path: str,
        data: Any = None,
        follow: bool = False,
        format: str | None = None,
        **extra: Any,
    ) -> Any:
        return await super().head(
            path, data=data, follow=follow, **_asgi_headers(extra)
        )

    async def trace(
        self, path: str, follow: bool = False, format: str | None = None, **extra: Any
    ) -> Any:
        return await super().trace(path, follow=follow, **_asgi_headers(extra))

    async def post(
        self,
        path: str,
        data: Any = None,
        format: str | None = None,
        content_type: str | None = None,
        follow: bool = False,
        **extra: Any,
    ) -> Any:
        return await self._encoded_request(
            "POST", path, data, format, content_type, follow, extra
        )

    async def put(
        self,
        path: str,
        data: Any = None,
        format: str | None = None,
        content_type: str | None = None,
        follow: bool = False,
        **extra: Any,
    ) -> Any:
        return await self._encoded_request(
            "PUT", path, data, format, content_type, follow, extra
        )

    async def patch(
        self,
        path: str,
        data: Any = None,
        format: str | None = None,
        content_type: str | None = None,
        follow: bool = False,
        **extra: Any,
    ) -> Any:
        return await self._encoded_request(
            "PATCH", path, data, format, content_type, follow, extra
        )

    async def delete(
        self,
        path: str,
        data: Any = None,
        format: str | None = None,
        content_type: str | None = None,
        follow: bool = False,
        **extra: Any,
    ) -> Any:
        return await self._encoded_request(
            "DELETE", path, data, format, content_type, follow, extra
        )

    async def options(
        self,
        path: str,
        data: Any = None,
        format: str | None = None,
        content_type: str | None = None,
        follow: bool = False,
        **extra: Any,
    ) -> Any:
        return await self._encoded_request(
            "OPTIONS", path, data, format, content_type, follow, extra
        )

    async def query(
        self,
        path: str,
        data: Any = None,
        format: str | None = None,
        content_type: str | None = None,
        follow: bool = False,
        **extra: Any,
    ) -> Any:
        """A QUERY request (RFC 10008), encoded like ``post()``."""
        return await self._encoded_request(
            "QUERY", path, data, format, content_type, follow, extra
        )

    async def _encoded_request(
        self,
        method: str,
        path: str,
        data: Any,
        format: str | None,
        content_type: str | None,
        follow: bool,
        extra: Any,
    ) -> Any:
        response = await self._api_request(
            method, path, data, format, content_type, **extra
        )
        if follow:
            # As DRF's ``APIClient`` hands them to Django's redirect handling.
            response = await self._ahandle_redirects(  # type: ignore[attr-defined]
                response, data=data, format=format, content_type=content_type, **extra
            )
        return response
