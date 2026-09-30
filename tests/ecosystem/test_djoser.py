"""djoser: users registered and logged in through its endpoints use aiodrf views."""

from django.test import TestCase, override_settings
from django.urls import include, path
from rest_framework.authentication import TokenAuthentication
from rest_framework.authtoken.models import Token
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.authentication import JWTAuthentication

from aiodrf.test import AsyncAPIClient, count_hops
from tests.base import both_transports
from tests.ecosystem.base import UserFixture, same_response, whoami_views

drf_token_whoami, token_whoami = whoami_views(
    authentication_classes=[TokenAuthentication], permission_classes=[IsAuthenticated]
)
drf_jwt_whoami, jwt_whoami = whoami_views(
    authentication_classes=[JWTAuthentication], permission_classes=[IsAuthenticated]
)
urlpatterns = [
    path("auth/", include("djoser.urls")),
    path("auth/", include("djoser.urls.authtoken")),
    path("auth/", include("djoser.urls.jwt")),
    path("drf/token/", drf_token_whoami),
    path("aiodrf/token/", token_whoami),
    path("drf/jwt/", drf_jwt_whoami),
    path("aiodrf/jwt/", jwt_whoami),
]
urls = override_settings(ROOT_URLCONF=__name__)


@both_transports
class _DjoserTests:
    async def register(self):
        response = await self.api(
            "post",
            "/auth/users/",
            data={
                "username": "ged",
                "password": "a-long-true-name",
                "email": "ged@example.com",
            },
        )
        assert response.status_code == 201, response.data

    @urls
    async def test_the_token_of_djosers_login_authenticates(self):
        await self.register()
        login = await self.api(
            "post",
            "/auth/token/login/",
            data={"username": "ged", "password": "a-long-true-name"},
        )
        assert login.status_code == 200, login.data
        for authorization in (f"Token {login.data['auth_token']}", "Token wrong", None):
            headers = (
                {} if authorization is None else {"HTTP_AUTHORIZATION": authorization}
            )
            with self.subTest(authorization=authorization):
                drf = await self.api("get", "/drf/token/", **headers)
                aiodrf = await self.api("get", "/aiodrf/token/", **headers)
                assert same_response(drf, aiodrf), (drf.data, aiodrf.data)
        assert aiodrf.status_code == 401

        headers = {"HTTP_AUTHORIZATION": f"Token {login.data['auth_token']}"}
        assert (await self.api("get", "/aiodrf/token/", **headers)).data == {
            "user": "ged"
        }
        # djoser's logout deletes the token (its view authenticates with DRF's
        # default classes, fixed at import; here the token is deleted directly).
        await Token.objects.filter(key=login.data["auth_token"]).adelete()
        assert (await self.api("get", "/aiodrf/token/", **headers)).status_code == 401

    @urls
    async def test_the_jwt_of_djosers_create_view_authenticates(self):
        await self.register()
        created = await self.api(
            "post",
            "/auth/jwt/create/",
            data={"username": "ged", "password": "a-long-true-name"},
        )
        assert created.status_code == 200, created.data
        headers = {"HTTP_AUTHORIZATION": f"Bearer {created.data['access']}"}
        drf = await self.api("get", "/drf/jwt/", **headers)
        aiodrf = await self.api("get", "/aiodrf/jwt/", **headers)
        assert same_response(drf, aiodrf)
        assert aiodrf.data == {"user": "ged"}


@urls
class HopTests(UserFixture, TestCase):
    async def test_token_authentication_loads_the_user_in_one_hop(self):
        token = await Token.objects.acreate(user=self.user)
        with count_hops() as hops:
            response = await AsyncAPIClient().get(
                "/aiodrf/token/", HTTP_AUTHORIZATION=f"Token {token.key}"
            )
        assert response.data == {"user": "ursula"}
        assert hops.count == 1, hops.calls
