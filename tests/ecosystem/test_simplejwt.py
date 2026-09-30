"""djangorestframework-simplejwt authenticating aiodrf views."""

from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import path
from django.utils.asyncio import async_unsafe
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.authentication import (
    JWTAuthentication,
    JWTStatelessUserAuthentication,
)
from rest_framework_simplejwt.tokens import AccessToken
from rest_framework_simplejwt.views import TokenObtainPairView

from aiodrf.test import AsyncAPIClient, AsyncAPIRequestFactory, count_hops
from aiodrf.utils import async_safe
from tests.base import both_transports
from tests.ecosystem.base import UserFixture, same_response, whoami_views


@async_safe
class StatelessJWTAuthentication(JWTStatelessUserAuthentication):
    # The recipe of ``aiodrf.contrib.simplejwt``: with the default settings
    # nothing in it does I/O.
    def authenticate(self, request):
        return super().authenticate(request)


drf_whoami, whoami = whoami_views(
    authentication_classes=[JWTAuthentication], permission_classes=[IsAuthenticated]
)
_, stateless_whoami = whoami_views(
    authentication_classes=[StatelessJWTAuthentication],
    permission_classes=[IsAuthenticated],
)
urlpatterns = [
    path("token/", TokenObtainPairView.as_view()),
    path("drf/", drf_whoami),
    path("aiodrf/", whoami),
    path("stateless/", stateless_whoami),
]
urls = override_settings(ROOT_URLCONF=__name__)


@both_transports
class _ParityTests(UserFixture):
    @urls
    async def test_expired_tokens_are_rejected_before_the_action(self):
        token = AccessToken.for_user(self.user)
        token.set_exp(lifetime=timedelta(seconds=-1))
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"}
        drf = await self.api("get", "/drf/", **headers)
        response = await self.api("get", "/aiodrf/", **headers)
        assert response.status_code == 401
        assert same_response(drf, response)

    @urls
    async def test_the_token_of_simplejwts_own_view_authenticates(self):
        response = await self.api(
            "post", "/token/", data={"username": "ursula", "password": "earthsea"}
        )
        assert response.status_code == 200, response.data
        bearer = f"Bearer {response.data['access']}"
        response = await self.api("get", "/aiodrf/", HTTP_AUTHORIZATION=bearer)
        assert response.data == {"user": "ursula"}

    @urls
    async def test_aiodrf_answers_like_drf(self):
        valid = f"Bearer {AccessToken.for_user(self.user)}"
        for authorization in (valid, "Bearer garbage", "Token other-scheme", None):
            headers = (
                {} if authorization is None else {"HTTP_AUTHORIZATION": authorization}
            )
            with self.subTest(authorization=authorization):
                drf = await self.api("get", "/drf/", **headers)
                aiodrf = await self.api("get", "/aiodrf/", **headers)
                assert same_response(drf, aiodrf), (drf.data, aiodrf.data)
                assert drf.status_code == (200 if authorization == valid else 401)


@urls
class HopTests(UserFixture, TestCase):
    async def test_without_a_token_authentication_costs_no_hop(self):
        for headers in ({}, {"HTTP_AUTHORIZATION": "Token other-scheme"}):
            with self.subTest(headers=headers), count_hops() as hops:
                response = await AsyncAPIClient().get("/aiodrf/", **headers)
            assert response.status_code == 401
            assert hops.calls == []

    async def test_with_a_token_the_user_is_loaded_in_one_hop(self):
        bearer = f"Bearer {AccessToken.for_user(self.user)}"
        with count_hops() as hops:
            response = await AsyncAPIClient().get("/aiodrf/", HTTP_AUTHORIZATION=bearer)
        assert response.data == {"user": "ursula"}
        assert hops.count == 1, hops.calls

    async def test_stateless_authentication_declared_pure_costs_no_hop(self):
        bearer = f"Bearer {AccessToken.for_user(self.user)}"
        with count_hops() as hops:
            response = await AsyncAPIClient().get(
                "/stateless/", HTTP_AUTHORIZATION=bearer
            )
        assert response.status_code == 200, response.data
        assert hops.calls == []


class HeaderFromElsewhere(JWTAuthentication):
    # A project's own header lookup, which may do I/O.
    @async_unsafe("get_header ran on the event loop")
    def get_header(self, request):
        return super().get_header(request)


class SubclassTests(UserFixture, TestCase):
    async def test_a_subclass_with_its_own_header_lookup_is_asked_in_a_thread(self):
        _, view = whoami_views(
            authentication_classes=[HeaderFromElsewhere],
            permission_classes=[IsAuthenticated],
        )
        with count_hops() as hops:
            response = await view(AsyncAPIRequestFactory().get("/"))
        assert response.status_code == 401
        assert hops.count == 1, hops.calls
        bearer = f"Bearer {AccessToken.for_user(self.user)}"
        response = await view(
            AsyncAPIRequestFactory().get("/", HTTP_AUTHORIZATION=bearer)
        )
        assert response.data == {"user": "ursula"}
