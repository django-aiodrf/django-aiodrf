"""django-cors-headers in front of aiodrf views."""

import pytest
from corsheaders.defaults import default_methods
from django.test import override_settings
from django.urls import path
from rest_framework.permissions import AllowAny

from aiodrf.response import Response
from aiodrf.test import AsyncAPIClient
from aiodrf.views import APIView
from tests.base import both_transports
from tests.ecosystem.base import whoami_views

drf_whoami, whoami = whoami_views(
    authentication_classes=[], permission_classes=[AllowAny]
)
urlpatterns = [path("drf/", drf_whoami), path("aiodrf/", whoami)]
ORIGIN = "https://app.example"
cors = override_settings(
    ROOT_URLCONF=__name__,
    MIDDLEWARE=[
        "corsheaders.middleware.CorsMiddleware",
        "django.middleware.common.CommonMiddleware",
    ],
    CORS_ALLOWED_ORIGINS=[ORIGIN],
)


@both_transports
class _CorsTests:
    @cors
    async def test_responses_carry_the_cors_headers(self):
        for url in ("/drf/", "/aiodrf/"):
            with self.subTest(url=url):
                response = await self.api("get", url, HTTP_ORIGIN=ORIGIN)
                assert response.status_code == 200
                assert response.headers["access-control-allow-origin"] == ORIGIN
                other = await self.api("get", url, HTTP_ORIGIN="https://other.example")
                assert "access-control-allow-origin" not in other.headers

    @cors
    async def test_preflight_requests_are_answered_by_the_middleware(self):
        for url in ("/drf/", "/aiodrf/"):
            with self.subTest(url=url):
                response = await self.api(
                    "options",
                    url,
                    HTTP_ORIGIN=ORIGIN,
                    HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET",
                )
                assert response.status_code == 200
                assert response.headers["access-control-allow-origin"] == ORIGIN
                assert "GET" in response.headers["access-control-allow-methods"]
                assert response.content == b""


@pytest.mark.parametrize("position", [0, 1, 2])
async def test_cors_before_between_and_after_grouped_middleware(position):
    middleware = [
        "aiodrf.unsafe.middleware.SecurityMiddleware",
        "aiodrf.unsafe.middleware.CommonMiddleware",
    ]
    middleware.insert(position, "corsheaders.middleware.CorsMiddleware")
    with override_settings(
        ROOT_URLCONF=__name__,
        MIDDLEWARE=middleware,
        AIODRF={"UNSAFE_SYNC_MIDDLEWARE": True},
        FASTDRF={},
        CORS_ALLOWED_ORIGINS=[ORIGIN],
    ):
        client = AsyncAPIClient()
        for url in ("/drf/", "/aiodrf/"):
            response = await client.get(url, headers={"origin": ORIGIN})
            assert response.status_code == 200
            assert response["access-control-allow-origin"] == ORIGIN
            denied = await client.get(url, headers={"origin": "https://other.example"})
            assert "access-control-allow-origin" not in denied.headers
            preflight = await client.options(
                url, headers={"origin": ORIGIN, "access-control-request-method": "GET"}
            )
            assert preflight.status_code == 200
            assert preflight.content == b""
            assert preflight["access-control-allow-origin"] == ORIGIN


class Search(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    async def query(self, request):
        return Response({"received": request.data})


urlpatterns.append(path("search/", Search.as_view()))


@pytest.mark.parametrize("configured", [False, True])
async def test_query_needs_a_preflight_and_a_configured_method(configured):
    # RFC 10008 section 4: QUERY is not CORS-safelisted, so browsers send a
    # preflight; django-cors-headers allows only the methods it is given.
    settings = {"CORS_ALLOW_METHODS": [*default_methods, "QUERY"]} if configured else {}
    with cors, override_settings(**settings):
        client = AsyncAPIClient()
        preflight = await client.options(
            "/search/",
            headers={"origin": ORIGIN, "access-control-request-method": "QUERY"},
        )
        allowed = preflight["access-control-allow-methods"].split(", ")
        assert ("QUERY" in allowed) is configured
        response = await client.query(
            "/search/", {"q": 1}, format="json", headers={"origin": ORIGIN}
        )
        assert response.json() == {"received": {"q": 1}}
        assert response["access-control-allow-origin"] == ORIGIN
