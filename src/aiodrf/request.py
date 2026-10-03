"""DRF request access with awaitable authentication and parsing."""

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from django.conf import settings
from rest_framework import exceptions, request
from rest_framework.request import (  # type: ignore[attr-defined]
    _hasattr,
    wrap_attributeerrors,
)

from aiodrf import policies
from aiodrf.hooks import require_sync_hooks
from aiodrf.utils import is_pure, run_sync

if TYPE_CHECKING:
    from rest_framework.authentication import BaseAuthentication
    from rest_framework.negotiation import BaseContentNegotiation
    from rest_framework.parsers import BaseParser

__all__ = ["Request"]


class Request(request.Request):
    """
    DRF's ``Request`` with async accessors for lazily computed attributes.

    The synchronous ``user``, ``auth`` and ``data`` properties keep working;
    once the async variant has run they are plain cache lookups.
    """

    if TYPE_CHECKING:
        # Attributes DRF's ``__init__`` sets (``parsers or ()``, ...) and the
        # private members it has; its type stubs are looser or silent.
        parsers: Sequence[BaseParser]
        authenticators: Sequence[BaseAuthentication]
        negotiator: BaseContentNegotiation
        _auth: Any
        _full_data: Any

        def _not_authenticated(self) -> None: ...

        def _load_data_and_files(self) -> None: ...

    async def auser(self) -> Any:
        """
        Return the user authenticated by the view's authentication classes.

        This shadows ``HttpRequest.auser``, which Django's
        ``AuthenticationMiddleware`` installs and which only knows about
        session authentication.
        """
        if not hasattr(self, "_user"):
            await self._aauthenticate()
        return self._user

    async def aauth(self) -> Any:
        if not hasattr(self, "_auth"):
            await self._aauthenticate()
        return self._auth

    async def adata(self) -> Any:
        if not _hasattr(self, "_full_data"):
            with wrap_attributeerrors():
                if self._parses_inline():
                    self._load_data_and_files()
                else:
                    await run_sync(self._load_data_and_files)()
        return self._full_data

    def _parse(self) -> Any:
        # Also covers parsers returned dynamically by get_parsers(), and
        # synchronous access to request.data in DRF/schema callers.
        for parser in self.parsers:
            require_sync_hooks(parser, "parse")
        return super()._parse()  # type: ignore[misc]  # private in DRF, absent from its stubs

    def _parses_inline(self) -> bool:
        """
        Return True if reading ``data`` is CPU work: DRF's own JSON or form
        parser (the class itself; a subclass may do anything) is selected
        for a body Django kept in memory. Django's ASGI handler spools
        bodies larger than ``FILE_UPLOAD_MAX_MEMORY_SIZE`` to disk, and
        multipart upload handlers write files.
        """
        try:
            length = int(self.META.get("CONTENT_LENGTH") or 0)
        except ValueError:
            return False
        if not 0 < length <= settings.FILE_UPLOAD_MAX_MEMORY_SIZE:
            # Unknown (chunked) or large; an empty body is not parsed at all,
            # which DRF decides while it looks at the stream.
            return length == 0 and "HTTP_TRANSFER_ENCODING" not in self.META
        negotiator = self.negotiator
        if not is_pure(negotiator, "select_parser"):
            return False
        parser = negotiator.select_parser(self, self.parsers)
        return parser is not None and is_pure(parser, "parse")

    def _authenticate(self) -> None:
        # DRF's ``_authenticate``, reaching authenticators that only
        # implement ``aauthenticate``.
        for authenticator in self.authenticators:
            try:
                user_auth_tuple = policies.authenticate(authenticator, self)
            except exceptions.APIException:
                self._not_authenticated()
                raise

            if user_auth_tuple is not None:
                self._authenticator = authenticator
                self.user, self.auth = user_auth_tuple
                return

        self._not_authenticated()

    async def _aauthenticate(self) -> None:
        """Async counterpart of ``Request._authenticate``."""
        for authenticator in self.authenticators:
            try:
                user_auth_tuple = await policies.aauthenticate(authenticator, self)
            except exceptions.APIException:
                self._not_authenticated()
                raise

            if user_auth_tuple is not None:
                self._authenticator = authenticator
                self.user, self.auth = user_auth_tuple
                return

        self._not_authenticated()

    @property
    def user(self) -> Any:
        return super().user

    @user.setter
    def user(self, value: Any) -> None:
        self._user = value
        self._request.user = value

        async def auser() -> Any:
            return value

        # Keep ``await request._request.auser()`` (used by async middleware
        # and Django's auth decorators) consistent with DRF's user.
        self._request.auser = auser
