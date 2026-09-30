"""Benchmark-only alternative: inline stock header/URL hooks.

Not a supported middleware API: overrides or lazy URL resolution may block.
Sessions, authentication, CSRF and messages remain entirely stock Django.
"""

from django.middleware.clickjacking import (
    XFrameOptionsMiddleware as DjangoXFrameOptionsMiddleware,
)
from django.middleware.common import CommonMiddleware as DjangoCommonMiddleware
from django.middleware.security import SecurityMiddleware as DjangoSecurityMiddleware


class InlineHooks:
    async def __acall__(self, request):
        response = None
        if hasattr(self, "process_request"):
            response = self.process_request(request)
        response = response or await self.get_response(request)
        if hasattr(self, "process_response"):
            response = self.process_response(request, response)
        return response


class SecurityMiddleware(InlineHooks, DjangoSecurityMiddleware):
    pass


class CommonMiddleware(InlineHooks, DjangoCommonMiddleware):
    pass


class XFrameOptionsMiddleware(InlineHooks, DjangoXFrameOptionsMiddleware):
    pass
