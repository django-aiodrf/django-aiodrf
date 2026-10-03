"""
djangorestframework-camel-case: parser and renderer subclasses that rewrite
keys, and ``CamelCaseMiddleWare``, a synchronous middleware that replaces
``request.GET``.
"""

from django.test import override_settings
from django.urls import path
from djangorestframework_camel_case.parser import CamelCaseJSONParser
from djangorestframework_camel_case.render import CamelCaseJSONRenderer
from rest_framework import views as drf_views
from rest_framework.permissions import AllowAny
from rest_framework.response import Response as DRFResponse

from aiodrf import serializers
from aiodrf.response import Response
from aiodrf.test import AsyncAPIClient, count_hops
from aiodrf.views import APIView
from tests.base import both_transports


class ReadingSerializer(serializers.Serializer):
    first_name = serializers.CharField()
    page_count = serializers.IntegerField()
    favourite_books = serializers.ListField(child=serializers.DictField())


class Policies:
    authentication_classes = []
    permission_classes = [AllowAny]
    parser_classes = [CamelCaseJSONParser]
    renderer_classes = [CamelCaseJSONRenderer]


class DRFEcho(Policies, drf_views.APIView):
    def get(self, request):
        return DRFResponse({"query_params": request.query_params.dict()})

    def post(self, request):
        serializer = ReadingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return DRFResponse(serializer.validated_data)


class Echo(Policies, APIView):
    async def get(self, request):
        return Response({"query_params": request.query_params.dict()})

    async def post(self, request):
        serializer = ReadingSerializer(data=await request.adata())
        await serializer.ais_valid(raise_exception=True)
        return Response(serializer.validated_data)


urlpatterns = [path("drf/", DRFEcho.as_view()), path("aiodrf/", Echo.as_view())]
READING = {
    "firstName": "Ursula",
    "pageCount": 180,
    "favouriteBooks": [{"bookTitle": "Earthsea", "publishedIn": 1968}],
}
middleware = override_settings(
    ROOT_URLCONF=__name__,
    MIDDLEWARE=["djangorestframework_camel_case.middleware.CamelCaseMiddleWare"],
)


@both_transports
class _CamelCaseTests:
    @override_settings(ROOT_URLCONF=__name__)
    async def test_keys_round_trip_as_in_drf(self):
        drf = await self.api("post", "/drf/", data=READING, format="json")
        aiodrf = await self.api("post", "/aiodrf/", data=READING, format="json")
        assert aiodrf.status_code == drf.status_code == 200
        assert aiodrf.content == drf.content
        assert aiodrf.json() == READING

    @override_settings(ROOT_URLCONF=__name__)
    async def test_validation_errors_are_camel_cased_too(self):
        drf = await self.api("post", "/drf/", data={"firstName": "U"}, format="json")
        aiodrf = await self.api(
            "post", "/aiodrf/", data={"firstName": "U"}, format="json"
        )
        assert aiodrf.status_code == drf.status_code == 400
        assert aiodrf.content == drf.content
        assert set(aiodrf.json()) == {"pageCount", "favouriteBooks"}

    @middleware
    async def test_the_middleware_underscoreizes_the_query_string(self):
        drf = await self.api("get", "/drf/?pageSize=2&sortOrder=asc")
        aiodrf = await self.api("get", "/aiodrf/?pageSize=2&sortOrder=asc")
        assert aiodrf.content == drf.content
        # Its keys are rewritten back to camelCase by the renderer.
        assert aiodrf.json() == {"queryParams": {"pageSize": "2", "sortOrder": "asc"}}


def rendered_in_a_thread():
    """Whether Django's ASGI handler would render the response in a thread."""
    from asgiref.sync import iscoroutinefunction

    response = Response({})
    response.accepted_renderer = CamelCaseJSONRenderer()
    response.accepted_media_type = "application/json"
    response.renderer_context = {}
    return not iscoroutinefunction(response.render)


@override_settings(ROOT_URLCONF=__name__)
async def test_unknown_parser_and_renderer_run_in_threads():
    # aiodrf does not know that these subclasses only rewrite keys: the
    # parser runs in a hop, and Django renders the response in a thread.
    with count_hops() as hops:
        response = await AsyncAPIClient().post("/aiodrf/", READING, format="json")
    assert response.status_code == 200
    assert hops.calls == ["Request._load_data_and_files"]
    assert rendered_in_a_thread()


@override_settings(
    ROOT_URLCONF=__name__,
    AIODRF={
        "PURE_POLICIES": ["djangorestframework_camel_case.parser.CamelCaseJSONParser"],
        "INLINE_RENDERERS": [
            "djangorestframework_camel_case.render.CamelCaseJSONRenderer"
        ],
    },
    FASTDRF={},
)
async def test_declared_pure_they_run_on_the_loop():
    with count_hops() as hops:
        response = await AsyncAPIClient().post("/aiodrf/", READING, format="json")
    assert response.json() == READING
    assert hops.calls == []
    assert not rendered_in_a_thread()
