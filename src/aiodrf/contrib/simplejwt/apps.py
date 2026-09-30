"""Django application registration and startup hooks."""

from typing import TYPE_CHECKING, Any

from django.apps import AppConfig

if TYPE_CHECKING:
    from rest_framework.request import Request


def has_token(authenticator: Any, request: "Request") -> bool:
    # The first two steps of ``JWTAuthentication.authenticate()``.
    header = authenticator.get_header(request)
    return header is not None and authenticator.get_raw_token(header) is not None


class SimpleJWTConfig(AppConfig):
    name = "aiodrf.contrib.simplejwt"
    label = "aiodrf_simplejwt"
    verbose_name = "aiodrf: Simple JWT"

    def ready(self) -> None:
        from rest_framework_simplejwt.authentication import JWTAuthentication

        from aiodrf.authentication import register_credentials_check
        from aiodrf.utils import register_pure_method

        register_credentials_check(JWTAuthentication, has_token)
        # Its constructor only looks the user model up in the app registry;
        # without this, building the authenticators would cost a thread hop.
        register_pure_method(JWTAuthentication, "__init__", leaf=True)
        # It formats the header from settings and calls no other method, so
        # a subclass's own methods (``get_header``) leave it pure.
        register_pure_method(JWTAuthentication, "authenticate_header", leaf=True)
