"""dj-rest-auth: its login view issues a JWT cookie, aiodrf views accept it."""

from dj_rest_auth.jwt_auth import JWTCookieAuthentication
from dj_rest_auth.views import LoginView
from django.test import override_settings
from django.urls import path
from rest_framework.permissions import IsAuthenticated

from tests.base import both_transports
from tests.ecosystem.base import UserFixture, same_response, whoami_views

drf_whoami, whoami = whoami_views(
    authentication_classes=[JWTCookieAuthentication],
    permission_classes=[IsAuthenticated],
)
urlpatterns = [
    path("login/", LoginView.as_view()),
    path("drf/", drf_whoami),
    path("aiodrf/", whoami),
]


@both_transports
class _CookieTests(UserFixture):
    @override_settings(ROOT_URLCONF=__name__)
    async def test_the_cookie_of_the_login_view_authenticates(self):
        anonymous = await self.api("get", "/aiodrf/")
        assert anonymous.status_code == 401
        assert same_response(anonymous, await self.api("get", "/drf/"))

        login = await self.api(
            "post", "/login/", data={"username": "ursula", "password": "earthsea"}
        )
        assert login.status_code == 200, login.data
        assert "auth" in self.client.cookies

        # The test client sends the cookie from now on.
        drf = await self.api("get", "/drf/")
        aiodrf = await self.api("get", "/aiodrf/")
        assert same_response(drf, aiodrf), (drf.data, aiodrf.data)
        assert aiodrf.data == {"user": "ursula"}
