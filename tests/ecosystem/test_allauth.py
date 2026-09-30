"""django-allauth headless: its app and browser logins authenticate aiodrf views."""

from allauth.headless.contrib.rest_framework.authentication import (
    XSessionTokenAuthentication,
)
from django.test import TestCase, override_settings
from django.urls import include, path
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated

from aiodrf.test import AsyncAPIClient, count_hops
from tests.base import both_transports
from tests.ecosystem.base import UserFixture, same_response, whoami_views

drf_app_whoami, app_whoami = whoami_views(
    authentication_classes=[XSessionTokenAuthentication],
    permission_classes=[IsAuthenticated],
)
drf_browser_whoami, browser_whoami = whoami_views(
    authentication_classes=[SessionAuthentication], permission_classes=[IsAuthenticated]
)
urlpatterns = [
    path("_allauth/", include("allauth.headless.urls")),
    path("drf/app/", drf_app_whoami),
    path("aiodrf/app/", app_whoami),
    path("drf/browser/", drf_browser_whoami),
    path("aiodrf/browser/", browser_whoami),
]
headless = override_settings(ROOT_URLCONF=__name__, HEADLESS_ONLY=True)
CREDENTIALS = {"username": "ursula", "password": "earthsea"}


@both_transports
class _HeadlessTests(UserFixture):
    @headless
    async def test_the_session_token_of_the_app_login_authenticates(self):
        login = await self.api("post", "/_allauth/app/v1/auth/login", data=CREDENTIALS)
        assert login.status_code == 200, login.json()
        token = login.json()["meta"]["session_token"]
        for value in (token, "not-a-session", None):
            headers = {} if value is None else {"HTTP_X_SESSION_TOKEN": value}
            with self.subTest(token=value):
                drf = await self.api("get", "/drf/app/", **headers)
                aiodrf = await self.api("get", "/aiodrf/app/", **headers)
                assert same_response(drf, aiodrf), (drf.data, aiodrf.data)
        assert aiodrf.status_code == 403

        headers = {"HTTP_X_SESSION_TOKEN": token}
        assert (await self.api("get", "/aiodrf/app/", **headers)).data == {
            "user": "ursula"
        }
        logout = await self.api("delete", "/_allauth/app/v1/auth/session", **headers)
        assert logout.status_code == 401
        assert (await self.api("get", "/aiodrf/app/", **headers)).status_code == 403

    @headless
    async def test_the_session_of_the_browser_login_authenticates(self):
        assert (await self.api("get", "/aiodrf/browser/")).status_code == 403
        login = await self.api(
            "post", "/_allauth/browser/v1/auth/login", data=CREDENTIALS
        )
        assert login.status_code == 200, login.json()
        drf = await self.api("get", "/drf/browser/")
        aiodrf = await self.api("get", "/aiodrf/browser/")
        assert same_response(drf, aiodrf)
        assert aiodrf.data == {"user": "ursula"}


@headless
class HopTests(UserFixture, TestCase):
    async def test_the_session_token_is_resolved_in_one_hop(self):
        client = AsyncAPIClient()
        login = await client.post("/_allauth/app/v1/auth/login", data=CREDENTIALS)
        headers = {"HTTP_X_SESSION_TOKEN": login.json()["meta"]["session_token"]}
        with count_hops() as hops:
            response = await client.get("/aiodrf/app/", **headers)
        assert response.data == {"user": "ursula"}
        assert hops.count == 1, hops.calls
