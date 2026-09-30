"""drf-orjson-renderer: a renderer and a parser aiodrf knows nothing about."""

from django.test import override_settings
from django.urls import include, path
from drf_orjson_renderer.parsers import ORJSONParser
from drf_orjson_renderer.renderers import ORJSONRenderer
from rest_framework import viewsets as drf_viewsets
from rest_framework.permissions import AllowAny
from rest_framework.routers import SimpleRouter

from aiodrf import viewsets
from tests.base import both_transports
from tests.testapp.models import Author
from tests.testapp.serializers import AuthorSerializer


class Policies:
    queryset = Author.objects.order_by("pk")
    serializer_class = AuthorSerializer
    authentication_classes = []
    permission_classes = [AllowAny]
    renderer_classes = [ORJSONRenderer]
    parser_classes = [ORJSONParser]


class DRFAuthors(Policies, drf_viewsets.ModelViewSet):
    pass


class Authors(Policies, viewsets.ModelViewSet):
    pass


drf_router, router = SimpleRouter(), SimpleRouter()
drf_router.register("authors", DRFAuthors, basename="drf-author")
router.register("authors", Authors, basename="author")
urlpatterns = [
    path("drf/", include(drf_router.urls)),
    path("aiodrf/", include(router.urls)),
]


@both_transports
class _OrjsonTests:
    @override_settings(ROOT_URLCONF=__name__)
    async def test_aiodrf_parses_and_renders_like_drf(self):
        for prefix in ("drf", "aiodrf"):
            created = await self.api(
                "post", f"/{prefix}/authors/", data={"name": "Ürsula"}
            )
            assert created.status_code == 201, created.content
        drf = await self.api("get", "/drf/authors/")
        aiodrf = await self.api("get", "/aiodrf/authors/")
        assert aiodrf["content-type"] == drf["content-type"] == "application/json"
        assert aiodrf.content == drf.content
        # orjson writes UTF-8 where DRF's renderer escapes nothing either,
        # but it never pads separators.
        assert '"name":"Ürsula"'.encode() in aiodrf.content
