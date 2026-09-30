"""Django application registration and startup hooks."""

from typing import TYPE_CHECKING, Any

from django.apps import AppConfig

if TYPE_CHECKING:
    from rest_framework.request import Request


def has_token(authenticator: Any, request: "Request", cookie_name: str) -> bool:
    # ``AuthKitCookieAuthentication.authenticate_with_cookie()`` up to the
    # point where it returns None for a request without a token.
    header = authenticator.get_header(request)
    if header is None and cookie_name:
        return bool(request.COOKIES.get(cookie_name))
    return authenticator.get_raw_token(header) is not None


def has_jwt(authenticator: Any, request: "Request") -> bool:
    from auth_kit.cookie_profiles import resolve_cookie_profile

    return has_token(
        authenticator, request, resolve_cookie_profile(request).jwt_cookie_name
    )


def has_drf_token(authenticator: Any, request: "Request") -> bool:
    from auth_kit.cookie_profiles import resolve_cookie_profile

    return has_token(
        authenticator, request, resolve_cookie_profile(request).token_cookie_name
    )


class AuthKitConfig(AppConfig):
    name = "aiodrf.contrib.auth_kit"
    label = "aiodrf_auth_kit"
    verbose_name = "aiodrf: drf-auth-kit"

    def ready(self) -> None:
        from auth_kit.authentication import (
            JWTCookieAuthentication,
            TokenCookieAuthentication,
        )
        from rest_framework_simplejwt.authentication import JWTAuthentication

        from aiodrf.authentication import register_credentials_check
        from aiodrf.utils import register_pure_method

        register_credentials_check(JWTCookieAuthentication, has_jwt)
        register_credentials_check(TokenCookieAuthentication, has_drf_token)
        # Both classes inherit these from Simple JWT, as aiodrf.contrib.simplejwt
        # declares them: the constructor only looks the user model up in the
        # app registry, and ``authenticate_header`` formats the header from
        # settings (``TokenCookieAuthentication`` takes DRF's instead).
        register_pure_method(
            JWTAuthentication, "__init__", "authenticate_header", leaf=True
        )
