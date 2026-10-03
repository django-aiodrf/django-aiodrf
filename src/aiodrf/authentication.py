"""DRF authentication hooks with async adaptation and challenge support."""

from collections.abc import Callable
from typing import Any

from django.conf import settings
from django.utils.functional import SimpleLazyObject
from rest_framework import authentication
from rest_framework.authentication import (  # noqa: F401
    BasicAuthentication,
    CSRFCheck,
    RemoteUserAuthentication,
    TokenAuthentication,
    get_authorization_header,
)
from rest_framework.request import Request, _hasattr  # type: ignore[attr-defined]

from aiodrf.utils import (
    _defines_code,
    bridge_base,
    bridges_to,
    call_pair,
    class_cache,
    definer,
    depends_on_classification,
    run_sync,
    uses_sync,
)

__all__ = [
    "BaseAuthentication",
    "BasicAuthentication",
    "RemoteUserAuthentication",
    "SessionAuthentication",
    "TokenAuthentication",
    "has_authorization_scheme",
    "register_credentials_check",
]

bridge_base(authentication.BaseAuthentication)

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

# class defining ``authenticate()`` -> has_credentials(authenticator, request)
_CREDENTIAL_CHECKS: dict[type, Callable[[object, Request], bool]] = {}


def register_credentials_check(
    authentication_class: type[authentication.BaseAuthentication],
    has_credentials: Callable[[Any, Request], bool],
) -> None:
    """
    Tell aiodrf how to see that a request carries nothing for
    ``authentication_class``.

    Header-based authentication returns None, without any I/O, when the
    request does not carry its scheme, and only then. Given
    ``has_credentials(authenticator, request)``, which must not do I/O
    either, aiodrf skips the thread hop for ``authenticate()`` on such
    requests. The check applies while ``authenticate()`` is the one
    ``authentication_class`` defines and the subclasses in between add data
    only (``keyword = "Bearer"``): the check may call their methods
    (``get_header()``), so a subclass that adds code is asked in a thread.

    Registrations belong in ``AppConfig.ready()``; ``aiodrf.contrib.simplejwt``
    and ``aiodrf.contrib.knox`` are apps that do nothing else.
    """
    _CREDENTIAL_CHECKS[authentication_class] = has_credentials


def _set_on(obj: object, name: str) -> bool:
    """True if ``obj`` itself holds ``name``: not the class's method."""
    return name in getattr(obj, "__dict__", {})


@class_cache
def _adds_code(cls: type, klass: type) -> bool:
    # A subclass may change what the check calls on the authenticator.
    for subclass in cls.__mro__:
        if subclass is klass:
            return False
        if _defines_code(subclass):
            return True
    return False


# Results of :func:`_authentication_kind` other than a class to look up.
_SESSION = "session"
_CALL = "call"


@depends_on_classification
@class_cache
def _authentication_kind(cls: type) -> type | str:
    """
    How ``policies.aauthenticate`` treats instances of ``cls`` without state
    of their own, decided once per class: :data:`_SESSION` (DRF's session
    authentication), :data:`_CALL` (its ``authenticate``/``aauthenticate``), or
    the class defining ``authenticate`` whose registered credentials check is
    looked up on each request (registrations may come later).
    """
    if not uses_sync(cls, "authenticate", "aauthenticate"):
        return _CALL
    klass = definer(cls, "authenticate")
    if klass is authentication.SessionAuthentication:
        return _SESSION
    if klass is None or _adds_code(cls, klass):
        return _CALL
    return klass


def has_authorization_scheme(request: Request, keyword: bytes) -> bool:
    """True if the ``Authorization`` header starts with ``keyword`` (bytes, any case)."""
    auth = get_authorization_header(request).split()
    return bool(auth) and auth[0].lower() == keyword.lower()


register_credentials_check(
    authentication.BasicAuthentication,
    lambda authenticator, request: has_authorization_scheme(request, b"basic"),
)
register_credentials_check(
    authentication.TokenAuthentication,
    lambda authenticator, request: has_authorization_scheme(
        request, authenticator.keyword.encode()
    ),
)


@bridge_base
class BaseAuthentication(authentication.BaseAuthentication):
    """
    Base class for authentication implemented with ``async def aauthenticate``.

    The synchronous ``authenticate`` delegates to it, so the class still works
    where DRF authenticates synchronously (sync views, the browsable API).
    """

    @bridges_to("aauthenticate")
    def authenticate(self, request: Request) -> Any:
        return super().authenticate(request)

    async def aauthenticate(self, request: Request) -> Any:
        raise NotImplementedError(".aauthenticate() must be overridden.")

    @bridges_to("aauthenticate_header")
    def authenticate_header(self, request: Request) -> Any:
        return super().authenticate_header(request)

    async def aauthenticate_header(self, request: Request) -> str | None:
        """Return the challenge used for a 401 response, or None for 403."""
        return None


class SessionAuthentication(authentication.SessionAuthentication):
    """
    DRF's session authentication without a thread hop for the CSRF check.

    Plain ``rest_framework.authentication.SessionAuthentication`` gets the same
    treatment from aiodrf views; this class exists for explicit imports.
    """

    async def aauthenticate(self, request: Request) -> Any:
        return await asession_authenticate(self, request)


async def asession_authenticate(
    authenticator: authentication.SessionAuthentication, request: Request
) -> tuple[Any, None] | None:
    """Async counterpart of ``SessionAuthentication.authenticate``."""
    django_request = request._request
    user = getattr(django_request, "user", None)
    auser = getattr(django_request, "auser", None)
    # Match DRF when middleware explicitly assigns a user (including logout).
    # Before Django 6.0, alogin/alogout leave auser's cached value stale.
    # Only ask the async accessor to resolve an absent user, or the lazy one
    # Django's AuthenticationMiddleware set: its auser() answers the same.
    setup_module = _lazy_setup_module(user)
    if auser is not None and (
        user is None or setup_module == "django.contrib.auth.middleware"
    ):
        user = await auser()
    elif isinstance(user, SimpleLazyObject) and setup_module not in _PASS_THROUGH_USERS:
        # Another middleware's (a token, a blocked client): DRF evaluates it
        # in its thread, and so it may query.
        await run_sync(bool)(user)  # evaluated once, like any first use

    # Unauthenticated, CSRF validation not required.
    if not user or not user.is_active:
        return None

    if definer(
        type(authenticator), "enforce_csrf"
    ) is authentication.SessionAuthentication and not _set_on(
        authenticator, "enforce_csrf"
    ):
        if _csrf_check_is_pure(request):
            authenticator.enforce_csrf(request)
        else:
            await run_sync(authenticator.enforce_csrf)(request)
    else:
        await call_pair(authenticator, "enforce_csrf", "aenforce_csrf", request)

    # CSRF passed with authenticated user.
    return (user, None)


# Lazy users that only hand back a user their middleware has already read:
# django-structlog's ``RequestMiddleware`` re-wraps ``request.user`` after
# binding its id, to reset ``session.accessed``. Evaluating them does no I/O,
# and the user inside (a later middleware's too) is kept.
_PASS_THROUGH_USERS = frozenset({"django_structlog.middlewares.request"})


def _lazy_setup_module(user: object) -> str | None:
    setup = (
        user.__dict__.get("_setupfunc") if isinstance(user, SimpleLazyObject) else None
    )
    return getattr(setup, "__module__", None)


def _csrf_check_is_pure(request: Request) -> bool:
    """
    Django's CSRF check reads the secret from the cookie and the token from
    ``request.POST`` (for ``POST`` requests, before it looks at the header).
    It only does I/O when the secret lives in the session or when reading
    ``POST`` means parsing a multipart body.
    """
    if settings.CSRF_USE_SESSIONS:
        return False
    if request.method in _SAFE_METHODS or _hasattr(request, "_data"):
        return True
    parses_inline = getattr(request, "_parses_inline", None)
    return parses_inline is not None and parses_inline()
