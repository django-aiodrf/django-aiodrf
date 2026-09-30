"""Django application registration and startup hooks."""

from typing import TYPE_CHECKING, Any

from django.apps import AppConfig

if TYPE_CHECKING:
    from rest_framework.request import Request


def has_token(authenticator: Any, request: "Request") -> bool:
    from aiodrf.authentication import has_authorization_scheme

    # knox compares the scheme with its ``authenticate_header()``.
    return has_authorization_scheme(
        request, authenticator.authenticate_header(request).encode()
    )


class KnoxConfig(AppConfig):
    name = "aiodrf.contrib.knox"
    label = "aiodrf_knox"
    verbose_name = "aiodrf: knox"

    def ready(self) -> None:
        from knox.auth import TokenAuthentication

        from aiodrf.authentication import register_credentials_check
        from aiodrf.utils import register_pure_method

        register_credentials_check(TokenAuthentication, has_token)
        # It formats the header from settings and calls no other method, so
        # a subclass's own methods (``get_header``) leave it pure.
        register_pure_method(TokenAuthentication, "authenticate_header", leaf=True)
