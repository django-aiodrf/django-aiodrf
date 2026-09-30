"""aiodrf's async test client and request factory accept DRF's header style."""

import pytest
from asgiref.sync import sync_to_async
from django.http import HttpResponseRedirect
from django.test import override_settings
from django.urls import path
from rest_framework.permissions import AllowAny

from aiodrf.response import Response
from aiodrf.test import AsyncAPIClient, AsyncAPIRequestFactory
from aiodrf.views import APIView


class Echo(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    async def get(self, request):
        return Response({}, headers={"X-Seen": request.headers.get("X-Probe", "")})

    head = get
    trace = get
    post = put = patch = delete = options = get


urlpatterns = [path("echo/", Echo.as_view())]
METHODS = ["get", "head", "post", "put", "patch", "delete", "options", "trace"]


@pytest.mark.parametrize("method", METHODS)
async def test_the_factory_passes_wsgi_style_headers_for_every_method(method):
    factory = AsyncAPIRequestFactory()
    request = getattr(factory, method)("/echo/", HTTP_X_PROBE="yes")
    response = await Echo.as_view(http_method_names=METHODS)(request)
    assert response["X-Seen"] == "yes"


@pytest.mark.parametrize("method", METHODS)
@override_settings(ROOT_URLCONF=__name__)
async def test_the_client_passes_wsgi_style_headers_for_every_method(method):
    response = await getattr(AsyncAPIClient(), method)("/echo/", HTTP_X_PROBE="yes")
    assert response["X-Seen"] == "yes"


@pytest.mark.parametrize(
    "defaults",
    [{"HTTP_X_PROBE": "yes"}, {"headers": {"X-Probe": "yes"}}],
    ids=["wsgi-style", "headers"],
)
@override_settings(ROOT_URLCONF=__name__)
async def test_the_constructors_headers_are_sent(defaults):
    # DRF's ``APIClient(HTTP_AUTHORIZATION=...)``; Django's async client
    # would send it as ``Http-Authorization``.
    response = await AsyncAPIClient(**defaults).get("/echo/")
    assert response["X-Seen"] == "yes"
    request = AsyncAPIRequestFactory(**defaults).get("/echo/")
    assert "HTTP_HTTP_X_PROBE" not in request.META
    assert (await Echo.as_view()(request))["X-Seen"] == "yes"


@override_settings(ROOT_URLCONF=__name__)
async def test_a_request_and_credentials_take_precedence_over_the_constructor():
    client = AsyncAPIClient(HTTP_X_PROBE="constructor")
    client.credentials(HTTP_X_PROBE="credentials")
    assert (await client.get("/echo/"))["X-Seen"] == "credentials"
    response = await client.get("/echo/", HTTP_X_PROBE="request")
    assert response["X-Seen"] == "request"


class Moved(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    async def post(self, request):
        return HttpResponseRedirect("/echo/")

    put = patch = delete = options = post


urlpatterns += [path("moved/", Moved.as_view())]


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete", "options"])
@override_settings(ROOT_URLCONF=__name__)
async def test_the_client_follows_redirects_of_encoded_requests(method):
    # As DRF's ``APIClient`` does, ``follow`` is not a header. (Django's async
    # client follows 307 and 308 by a WSGI key its requests lack.)
    response = await getattr(AsyncAPIClient(), method)(
        "/moved/", {"a": 1}, format="json", follow=True, HTTP_X_PROBE="yes"
    )
    assert response.status_code == 200
    assert response.redirect_chain == [("/echo/", 302)]
    assert response["X-Seen"] == "yes"


@override_settings(ROOT_URLCONF=__name__)
async def test_generic_takes_wsgi_style_headers_too():
    client = AsyncAPIClient()
    client.credentials(HTTP_X_PROBE="from credentials")
    response = await client.generic("GET", "/echo/", HTTP_X_PROBE="per request")
    assert response["X-Seen"] == "per request"


async def test_the_factorys_generic_takes_wsgi_style_headers_too():
    request = AsyncAPIRequestFactory().generic("GET", "/echo/", HTTP_X_PROBE="yes")
    response = await Echo.as_view()(request)
    assert response["X-Seen"] == "yes"


class Headers(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    async def get(self, request):
        return Response(sorted(request.headers))


class MovedToHeaders(Moved):
    async def post(self, request):
        return HttpResponseRedirect("/headers/")

    put = patch = delete = options = post


urlpatterns += [
    path("headers/", Headers.as_view()),
    path("moved-to-headers/", MovedToHeaders.as_view()),
]


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete", "options"])
@override_settings(ROOT_URLCONF=__name__)
async def test_the_client_follows_a_redirect_without_a_format(method):
    response = await getattr(AsyncAPIClient(), method)("/moved/", {"a": 1}, follow=True)
    assert response.status_code == 200
    assert response.redirect_chain == [("/echo/", 302)]


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete", "options"])
@override_settings(ROOT_URLCONF=__name__)
async def test_a_followed_request_carries_no_format_header(method):
    from aiodrf.test import APIClient

    ours = await getattr(AsyncAPIClient(), method)(
        "/moved-to-headers/", {"a": 1}, format="json", follow=True
    )
    theirs = await sync_to_async(getattr(APIClient(), method))(
        "/moved-to-headers/", {"a": 1}, format="json", follow=True
    )
    assert "Format" not in ours.json()
    assert "Content-Type" not in ours.json()
    assert "Format" not in theirs.json()


class Authorization(APIView):
    permission_classes = [AllowAny]

    async def get(self, request):
        return Response({"auth": request.headers.get("Authorization")})


urlpatterns += [path("authorization/", Authorization.as_view())]


@pytest.mark.django_db
@override_settings(ROOT_URLCONF=__name__)
async def test_alogout_clears_credentials():
    client = AsyncAPIClient()
    client.credentials(HTTP_AUTHORIZATION="Token abc")
    assert (await client.get("/authorization/")).json() == {"auth": "Token abc"}
    await client.alogout()
    assert (await client.get("/authorization/")).json() == {"auth": None}


@pytest.mark.django_db
async def test_alogout_clears_the_forced_user():
    from django.contrib.auth.models import User

    client = AsyncAPIClient()
    client.force_authenticate(User(username="u"), token="t")
    await client.alogout()
    assert client.handler._force_user is None
    assert client.handler._force_token is None


@pytest.mark.django_db
def test_logout_clears_credentials_and_the_forced_user():
    from django.contrib.auth.models import User

    client = AsyncAPIClient()
    client.credentials(HTTP_AUTHORIZATION="Token abc")
    client.force_authenticate(User(username="u"))
    client.logout()
    assert client._credentials == {}
    assert client.handler._force_user is None
