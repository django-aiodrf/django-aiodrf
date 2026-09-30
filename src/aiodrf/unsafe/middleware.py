"""Ask Django to group selected middleware into a synchronous chain.

Use these explicit paths in MIDDLEWARE and set UNSAFE_SYNC_MIDDLEWARE=True
in AIODRF before constructing the handler. Defaults preserve Django's dual
capability. No Django methods or settings are patched; all hooks are inherited.

This retains a synchronous call stack while an async view awaits. Measure your
workload and cancellation behavior before enabling it; it is not a threadless
middleware implementation. See docs/guides/unsafe-middleware.md.
"""

from django.contrib.auth.middleware import (
    AuthenticationMiddleware as DjangoAuthenticationMiddleware,
)
from django.contrib.messages.middleware import (
    MessageMiddleware as DjangoMessageMiddleware,
)
from django.contrib.sessions.middleware import (
    SessionMiddleware as DjangoSessionMiddleware,
)
from django.middleware.clickjacking import (
    XFrameOptionsMiddleware as DjangoXFrameOptionsMiddleware,
)
from django.middleware.common import CommonMiddleware as DjangoCommonMiddleware
from django.middleware.csrf import CsrfViewMiddleware as DjangoCsrfViewMiddleware
from django.middleware.security import SecurityMiddleware as DjangoSecurityMiddleware
from django.utils.functional import classproperty

from aiodrf.settings import aiodrf_settings


class _GroupedMiddleware:
    @classproperty
    def async_capable(cls) -> bool:
        # Django reads this while building the stack, not on every request.
        return not aiodrf_settings.UNSAFE_SYNC_MIDDLEWARE


class SecurityMiddleware(_GroupedMiddleware, DjangoSecurityMiddleware):
    pass


class SessionMiddleware(_GroupedMiddleware, DjangoSessionMiddleware):
    pass


class CommonMiddleware(_GroupedMiddleware, DjangoCommonMiddleware):
    pass


class CsrfViewMiddleware(_GroupedMiddleware, DjangoCsrfViewMiddleware):
    pass


class AuthenticationMiddleware(_GroupedMiddleware, DjangoAuthenticationMiddleware):
    pass


class MessageMiddleware(_GroupedMiddleware, DjangoMessageMiddleware):
    pass


class XFrameOptionsMiddleware(_GroupedMiddleware, DjangoXFrameOptionsMiddleware):
    pass
