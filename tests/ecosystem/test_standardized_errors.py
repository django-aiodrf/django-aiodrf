"""drf-standardized-errors as ``EXCEPTION_HANDLER`` of aiodrf views."""

from django.test import TestCase, override_settings
from django.urls import include, path
from rest_framework import viewsets as drf_viewsets
from rest_framework.permissions import IsAuthenticatedOrReadOnly
from rest_framework.routers import SimpleRouter

from aiodrf import viewsets
from aiodrf.test import AsyncAPIClient, count_hops
from tests.base import both_transports
from tests.ecosystem.base import UserFixture, same_response
from tests.testapp.models import Author
from tests.testapp.serializers import AuthorSerializer


class Policies:
    queryset = Author.objects.order_by("pk")
    serializer_class = AuthorSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]


class DRFAuthors(Policies, drf_viewsets.ModelViewSet):
    def destroy(self, request, *args, **kwargs):
        raise ZeroDivisionError("unexpected")


class Authors(Policies, viewsets.ModelViewSet):
    async def destroy(self, request, *args, **kwargs):
        raise ZeroDivisionError("unexpected")


drf_router, router = SimpleRouter(), SimpleRouter()
drf_router.register("authors", DRFAuthors, basename="drf-author")
router.register("authors", Authors, basename="author")
urlpatterns = [
    path("drf/", include(drf_router.urls)),
    path("aiodrf/", include(router.urls)),
]

standardized = override_settings(
    ROOT_URLCONF=__name__,
    REST_FRAMEWORK={
        "EXCEPTION_HANDLER": "drf_standardized_errors.handler.exception_handler"
    },
)


@both_transports
class _ParityTests(UserFixture):
    @standardized
    async def test_aiodrf_answers_like_drf(self):
        self.client.raise_request_exception = False
        requests = [
            ("get", "authors/404/", None, "not_found"),
            ("post", "authors/", {"name": "x"}, "not_authenticated"),
        ]
        for method, url, data, code in requests:
            with self.subTest(code=code):
                drf = await self.api(method, f"/drf/{url}", data=data)
                aiodrf = await self.api(method, f"/aiodrf/{url}", data=data)
                assert same_response(drf, aiodrf), (drf.data, aiodrf.data)
                assert aiodrf.data["errors"][0]["code"] == code

    @standardized
    async def test_validation_and_unexpected_errors(self):
        self.client.raise_request_exception = False
        self.client.force_authenticate(self.user)
        author = await Author.objects.acreate(name="Ursula")
        for method, url, data, error_type in [
            ("post", "authors/", {"name": ""}, "validation_error"),
            ("put", "authors/", None, "client_error"),
            # The handler turns what DRF would re-raise into a 500 response.
            ("delete", f"authors/{author.pk}/", None, "server_error"),
        ]:
            with self.subTest(type=error_type):
                drf = await self.api(method, f"/drf/{url}", data=data)
                aiodrf = await self.api(method, f"/aiodrf/{url}", data=data)
                assert same_response(drf, aiodrf), (drf.data, aiodrf.data)
                assert aiodrf.data["type"] == error_type


@standardized
class HopTests(TestCase):
    async def test_a_third_party_handler_runs_in_a_thread(self):
        # Unlike DRF's own handler it is not known to be free of I/O.
        with count_hops() as hops:
            response = await AsyncAPIClient().get("/aiodrf/authors/404/")
        assert response.status_code == 404
        assert hops.calls == ["RetrieveModelMixin._retrieve", "exception_handler"]
