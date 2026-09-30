"""django-oauth-toolkit: bearer tokens and scope permissions on aiodrf views."""

from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import path
from django.utils import timezone
from oauth2_provider.contrib.rest_framework import OAuth2Authentication, TokenHasScope
from oauth2_provider.models import get_access_token_model, get_application_model

from aiodrf.test import AsyncAPIClient, count_hops
from tests.base import both_transports
from tests.ecosystem.base import UserFixture, same_response, whoami_views

drf_whoami, whoami = whoami_views(
    authentication_classes=[OAuth2Authentication],
    permission_classes=[TokenHasScope],
    required_scopes=["read"],
)
urlpatterns = [path("drf/", drf_whoami), path("aiodrf/", whoami)]
urls = override_settings(ROOT_URLCONF=__name__)


class TokenFixture(UserFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        application = get_application_model().objects.create(
            name="tests",
            user=cls.user,
            client_type="confidential",
            authorization_grant_type="password",
        )
        for token, scope, lifetime in (
            ("reader", "read", timedelta(hours=1)),
            ("writer", "write", timedelta(hours=1)),
            ("expired", "read", timedelta(hours=-1)),
        ):
            get_access_token_model().objects.create(
                user=cls.user,
                application=application,
                token=token,
                scope=scope,
                expires=timezone.now() + lifetime,
            )


@both_transports
class _ParityTests(TokenFixture):
    @urls
    async def test_aiodrf_answers_like_drf(self):
        expected = {
            "reader": 200,
            "writer": 403,
            "expired": 401,
            "unknown": 401,
            None: 401,
        }
        for token, status in expected.items():
            headers = {} if token is None else {"HTTP_AUTHORIZATION": f"Bearer {token}"}
            with self.subTest(token=token):
                drf = await self.api("get", "/drf/", **headers)
                aiodrf = await self.api("get", "/aiodrf/", **headers)
                assert same_response(drf, aiodrf), (drf.data, aiodrf.data)
                assert drf.status_code == status

    @urls
    @override_settings(OAUTH2_PROVIDER={"ERROR_RESPONSE_WITH_SCOPES": True})
    async def test_the_missing_scope_is_reported(self):
        # ``TokenHasScope`` sets ``message`` on itself while it checks, in
        # aiodrf's worker thread; the view reads it afterwards.
        drf = await self.api("get", "/drf/", HTTP_AUTHORIZATION="Bearer writer")
        aiodrf = await self.api("get", "/aiodrf/", HTTP_AUTHORIZATION="Bearer writer")
        assert same_response(drf, aiodrf), (drf.data, aiodrf.data)
        assert aiodrf.data["required_scopes"] == ["read"]


@urls
class HopTests(TokenFixture, TestCase):
    async def test_hops(self):
        with count_hops() as hops:
            response = await AsyncAPIClient().get(
                "/aiodrf/", HTTP_AUTHORIZATION="Bearer reader"
            )
        assert response.data == {"user": "ursula"}
        # oauthlib verifies the token with queries; the permission is not
        # declared pure, so it is checked in a thread as well.
        assert hops.count == 2, hops.calls
        assert hops.calls[0] == "OAuth2Authentication.authenticate"
