"""drf-auth-kit's cookie authentication on aiodrf views."""

from contextlib import contextmanager
from unittest import mock

from auth_kit import authentication as auth_kit_authentication
from auth_kit.authentication import JWTCookieAuthentication, TokenCookieAuthentication
from django.test import TestCase, override_settings
from django.urls import path
from django.utils.asyncio import async_unsafe
from rest_framework.authtoken.models import Token
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.tokens import AccessToken

from aiodrf.test import AsyncAPIClient, AsyncAPIRequestFactory, count_hops
from tests.base import both_transports
from tests.ecosystem.base import UserFixture, same_response, whoami_views

# The profile ``tests/ecosystem/settings.py`` configures in ``AUTH_KIT``.
ADMIN_ORIGIN = "https://admin.example.com"

drf_jwt, jwt = whoami_views(
    authentication_classes=[JWTCookieAuthentication],
    permission_classes=[IsAuthenticated],
)
drf_token, token = whoami_views(
    authentication_classes=[TokenCookieAuthentication],
    permission_classes=[IsAuthenticated],
)
urlpatterns = [
    path("drf/jwt/", drf_jwt),
    path("aiodrf/jwt/", jwt),
    path("drf/token/", drf_token),
    path("aiodrf/token/", token),
]
urls = override_settings(ROOT_URLCONF=__name__)


@contextmanager
def auth_type(value):
    # auth-kit binds its settings object at import: ``override_settings``
    # does not reach it.
    with mock.patch.object(
        auth_kit_authentication.auth_kit_settings, "AUTH_TYPE", value
    ):
        yield


def cases(scheme, valid, cookie, admin_cookie):
    """``(name, headers, authenticates)`` for one of auth-kit's classes."""
    bearer = {"HTTP_AUTHORIZATION": f"Bearer {valid}"}
    return [
        ("nothing", {}, False),
        ("header", bearer, True),
        ("malformed header", {"HTTP_AUTHORIZATION": "Bearer"}, False),
        ("invalid header", {"HTTP_AUTHORIZATION": "Bearer garbage"}, False),
        ("other scheme", {"HTTP_AUTHORIZATION": f"{scheme} other"}, False),
        ("cookie", {"HTTP_COOKIE": f"{cookie}={valid}"}, True),
        ("empty cookie", {"HTTP_COOKIE": f"{cookie}="}, False),
        ("invalid cookie", {"HTTP_COOKIE": f"{cookie}=garbage"}, False),
        ("other cookie", {"HTTP_COOKIE": f"unrelated={valid}"}, False),
        ("header wins", {**bearer, "HTTP_COOKIE": f"{cookie}=garbage"}, True),
        (
            "other scheme hides the cookie",
            {
                "HTTP_AUTHORIZATION": f"{scheme} other",
                "HTTP_COOKIE": f"{cookie}={valid}",
            },
            False,
        ),
        (
            "profile cookie",
            {"HTTP_ORIGIN": ADMIN_ORIGIN, "HTTP_COOKIE": f"{admin_cookie}={valid}"},
            True,
        ),
        (
            "default cookie under a profile",
            {"HTTP_ORIGIN": ADMIN_ORIGIN, "HTTP_COOKIE": f"{cookie}={valid}"},
            False,
        ),
        (
            "default cookie of an unknown origin",
            {
                "HTTP_ORIGIN": "https://elsewhere.example.com",
                "HTTP_COOKIE": f"{cookie}={valid}",
            },
            True,
        ),
    ]


class AuthKitFixture(UserFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.jwt = str(AccessToken.for_user(cls.user))
        cls.token = Token.objects.create(user=cls.user).key


@both_transports
class _ParityTests(AuthKitFixture):
    async def assert_parity(self, kind, cases):
        for name, headers, authenticates in cases:
            with self.subTest(name):
                drf = await self.api("get", f"/drf/{kind}/", **headers)
                aiodrf = await self.api("get", f"/aiodrf/{kind}/", **headers)
                assert same_response(drf, aiodrf), (drf.data, aiodrf.data)
                assert drf.status_code == (200 if authenticates else 401), drf.data

    @urls
    async def test_jwt_cookie_authentication_answers_like_drf(self):
        await self.assert_parity(
            "jwt", cases("Token", self.jwt, "auth-jwt", "admin-jwt")
        )

    @urls
    async def test_token_cookie_authentication_answers_like_drf(self):
        with auth_type("token"):
            await self.assert_parity(
                "token", cases("Token", self.token, "auth-token", "admin-token")
            )


@urls
class HopTests(AuthKitFixture, TestCase):
    async def assert_hops(self, kind, headers, count):
        with count_hops() as hops:
            response = await AsyncAPIClient().get(f"/aiodrf/{kind}/", **headers)
        assert response.status_code == (200 if count else 401), response.data
        assert hops.count == count, hops.calls

    async def test_without_credentials_authentication_costs_no_hop(self):
        for kind in ("jwt", "token"):
            for headers in (
                {},
                {"HTTP_AUTHORIZATION": "Token other"},
                {"HTTP_COOKIE": "unrelated=value"},
                {"HTTP_ORIGIN": ADMIN_ORIGIN, "HTTP_COOKIE": f"auth-{kind}=value"},
            ):
                with self.subTest(kind=kind, headers=headers):
                    await self.assert_hops(kind, headers, 0)

    async def test_a_jwt_is_verified_and_its_user_loaded_in_one_hop(self):
        for headers in (
            {"HTTP_AUTHORIZATION": f"Bearer {self.jwt}"},
            {"HTTP_COOKIE": f"auth-jwt={self.jwt}"},
            {"HTTP_ORIGIN": ADMIN_ORIGIN, "HTTP_COOKIE": f"admin-jwt={self.jwt}"},
        ):
            with self.subTest(headers=headers):
                await self.assert_hops("jwt", headers, 1)

    async def test_a_token_is_looked_up_in_one_hop(self):
        with auth_type("token"):
            for headers in (
                {"HTTP_AUTHORIZATION": f"Bearer {self.token}"},
                {"HTTP_COOKIE": f"auth-token={self.token}"},
            ):
                with self.subTest(headers=headers):
                    await self.assert_hops("token", headers, 1)


class CookieFromElsewhere(JWTCookieAuthentication):
    # A project's own token lookup, which may do I/O.
    @async_unsafe("authenticate_with_cookie ran on the event loop")
    def authenticate_with_cookie(self, request, cookie_name):
        return super().authenticate_with_cookie(request, cookie_name)


class SubclassTests(AuthKitFixture, TestCase):
    async def test_a_subclass_with_its_own_lookup_is_asked_in_a_thread(self):
        _, view = whoami_views(
            authentication_classes=[CookieFromElsewhere],
            permission_classes=[IsAuthenticated],
        )
        with count_hops() as hops:
            response = await view(AsyncAPIRequestFactory().get("/"))
        assert response.status_code == 401
        assert hops.count == 1, hops.calls
        request = AsyncAPIRequestFactory().get("/", HTTP_COOKIE=f"auth-jwt={self.jwt}")
        response = await view(request)
        assert response.data == {"user": "ursula"}
