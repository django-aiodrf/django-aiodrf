"""
nested-multipart-parser: a ``MultiPartParser`` subclass that turns
``author[name]``/``books[0][title]`` keys and the uploaded files into one
nested ``QueryDict``, validated by nested serializers.
"""

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import path
from nested_multipart_parser.drf import DrfNestedParser
from rest_framework import views as drf_views
from rest_framework.permissions import AllowAny
from rest_framework.response import Response as DRFResponse

from aiodrf import serializers
from aiodrf.response import Response
from aiodrf.test import AsyncAPIClient, count_hops
from aiodrf.views import APIView
from tests.base import both_transports


class BookSerializer(serializers.Serializer):
    title = serializers.CharField()


class AuthorSerializer(serializers.Serializer):
    name = serializers.CharField()


class SubmissionSerializer(serializers.Serializer):
    author = AuthorSerializer()
    books = BookSerializer(many=True)
    cover = serializers.FileField()


def summary(request, validated):
    return {
        "author": validated["author"],
        "books": validated["books"],
        "cover": [validated["cover"].name, validated["cover"].read().decode()],
        "files": sorted(request.FILES),
        "data_type": type(request.data).__name__,
    }


class Policies:
    authentication_classes = []
    permission_classes = [AllowAny]
    parser_classes = [DrfNestedParser]


class DRFSubmit(Policies, drf_views.APIView):
    def post(self, request):
        serializer = SubmissionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return DRFResponse(summary(request, serializer.validated_data))


class Submit(Policies, APIView):
    async def post(self, request):
        serializer = SubmissionSerializer(data=await request.adata())
        await serializer.ais_valid(raise_exception=True)
        return Response(summary(request, serializer.validated_data))


urlpatterns = [path("drf/", DRFSubmit.as_view()), path("aiodrf/", Submit.as_view())]
# A plain dict, which nested serializers read; the default QueryDict wraps
# every nested value in a list.
nested = override_settings(
    ROOT_URLCONF=__name__, DRF_NESTED_MULTIPART_PARSER={"querydict": False}
)


def form(**overrides):
    return {
        "author.name": "Ursula",
        "books[0].title": "Earthsea",
        "books[1].title": "The Dispossessed",
        "cover": SimpleUploadedFile("cover.txt", b"a wizard"),
        **overrides,
    }


@both_transports
class _NestedMultipartTests:
    @nested
    async def test_nested_data_and_the_file_as_in_drf(self):
        drf = await self.api("post", "/drf/", data=form(), format="multipart")
        aiodrf = await self.api("post", "/aiodrf/", data=form(), format="multipart")
        assert aiodrf.status_code == drf.status_code == 200
        assert aiodrf.json() == drf.json()
        assert aiodrf.json() == {
            "author": {"name": "Ursula"},
            "books": [{"title": "Earthsea"}, {"title": "The Dispossessed"}],
            "cover": ["cover.txt", "a wizard"],
            # The parser returns the files inside ``data``, so DRF leaves
            # ``request.FILES`` empty; aiodrf does the same.
            "files": [],
            "data_type": "dict",
        }

    @nested
    async def test_malformed_keys_are_a_parse_error_as_in_drf(self):
        drf = await self.api(
            "post", "/drf/", data=form(**{"books[x": "?"}), format="multipart"
        )
        aiodrf = await self.api(
            "post", "/aiodrf/", data=form(**{"books[x": "?"}), format="multipart"
        )
        assert aiodrf.status_code == drf.status_code == 400
        assert aiodrf.json() == drf.json()


@nested
async def test_the_multipart_body_is_parsed_in_one_hop():
    with count_hops() as hops:
        response = await AsyncAPIClient().post("/aiodrf/", form(), format="multipart")
    assert response.status_code == 200
    # Multipart parsing may write files: it always runs in a thread.
    assert hops.calls == ["Request._load_data_and_files"]
