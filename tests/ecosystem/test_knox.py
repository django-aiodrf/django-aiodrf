"""django-rest-knox authenticating aiodrf views."""

from django.test import TestCase, override_settings
from django.urls import path
from knox.auth import TokenAuthentication
from knox.models import AuthToken
from rest_framework.permissions import IsAuthenticated

from aiodrf.test import AsyncAPIClient, count_hops
from tests.base import both_transports
from tests.ecosystem.base import UserFixture, same_response, whoami_views

drf_whoami, whoami = whoami_views(
    authentication_classes=[TokenAuthentication], permission_classes=[IsAuthenticated]
)
urlpatterns = [path("drf/", drf_whoami), path("aiodrf/", whoami)]
urls = override_settings(ROOT_URLCONF=__name__)


class KnoxFixture(UserFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        _, cls.token = AuthToken.objects.create(cls.user)


@both_transports
class _ParityTests(KnoxFixture):
    @urls
    async def test_revoked_tokens_are_rejected(self):
        await AuthToken.objects.filter(user=self.user).adelete()
        headers = {"HTTP_AUTHORIZATION": f"Token {self.token}"}
        drf = await self.api("get", "/drf/", **headers)
        response = await self.api("get", "/aiodrf/", **headers)
        assert response.status_code == 401
        assert same_response(drf, response)

    @urls
    async def test_aiodrf_answers_like_drf(self):
        valid = f"Token {self.token}"
        for authorization in (
            valid,
            "Token garbage",
            "Token",
            "Bearer other-scheme",
            None,
        ):
            headers = (
                {} if authorization is None else {"HTTP_AUTHORIZATION": authorization}
            )
            with self.subTest(authorization=authorization):
                drf = await self.api("get", "/drf/", **headers)
                aiodrf = await self.api("get", "/aiodrf/", **headers)
                assert same_response(drf, aiodrf), (drf.data, aiodrf.data)
                assert drf.status_code == (200 if authorization == valid else 401)


@urls
class HopTests(KnoxFixture, TestCase):
    async def test_without_a_token_authentication_costs_no_hop(self):
        for headers in ({}, {"HTTP_AUTHORIZATION": "Bearer other-scheme"}):
            with self.subTest(headers=headers), count_hops() as hops:
                response = await AsyncAPIClient().get("/aiodrf/", **headers)
            assert response.status_code == 401
            assert hops.calls == []

    async def test_with_a_token_it_is_looked_up_in_one_hop(self):
        with count_hops() as hops:
            response = await AsyncAPIClient().get(
                "/aiodrf/", HTTP_AUTHORIZATION=f"Token {self.token}"
            )
        assert response.data == {"user": "ursula"}
        assert hops.count == 1, hops.calls
