"""
File uploads through aiodrf views. Django's ASGI handler reads the whole
body before the view runs and its storages are synchronous: parsing the
multipart body and saving the file happen in the request's thread, like
any other DRF code, and nothing of it touches the event loop.
"""

import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import include, path
from rest_framework import serializers
from rest_framework import viewsets as drf_viewsets
from rest_framework.permissions import AllowAny
from rest_framework.routers import SimpleRouter

from aiodrf import viewsets
from aiodrf.test import AsyncAPIClient, count_hops
from tests.base import both_transports
from tests.ecosystem.base import same_response
from tests.testapp.models import Attachment


class AttachmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Attachment
        fields = ["id", "title", "file"]


class Policies:
    queryset = Attachment.objects.order_by("pk")
    serializer_class = AttachmentSerializer
    authentication_classes = []
    permission_classes = [AllowAny]


class DRFAttachments(Policies, drf_viewsets.ModelViewSet):
    pass


class Attachments(Policies, viewsets.ModelViewSet):
    pass


drf_router, router = SimpleRouter(), SimpleRouter()
drf_router.register("attachments", DRFAttachments, basename="drf-attachment")
router.register("attachments", Attachments, basename="attachment")
urlpatterns = [
    path("drf/", include(drf_router.urls)),
    path("aiodrf/", include(router.urls)),
]


class MediaRoot:
    @classmethod
    def setUpClass(cls):
        cls.enterClassContext(
            override_settings(
                ROOT_URLCONF=__name__,
                MEDIA_ROOT=cls.enterClassContext(tempfile.TemporaryDirectory()),
            )
        )
        super().setUpClass()


def upload(content=b"hello"):
    return {"title": "Notes", "file": SimpleUploadedFile("notes.txt", content)}


@both_transports
class _UploadTests(MediaRoot):
    async def test_aiodrf_stores_and_serves_like_drf(self):
        drf = await self.api(
            "post", "/drf/attachments/", data=upload(), format="multipart"
        )
        aiodrf = await self.api(
            "post", "/aiodrf/attachments/", data=upload(), format="multipart"
        )
        assert drf.status_code == aiodrf.status_code == 201, (drf.data, aiodrf.data)
        assert aiodrf.data["title"] == "Notes"
        assert aiodrf.data["file"].startswith(
            "http://testserver/media/attachments/notes"
        )
        stored = await Attachment.objects.aget(pk=aiodrf.data["id"])
        with stored.file.open("rb") as saved:
            assert saved.read() == b"hello"

        listed = await self.api("get", "/aiodrf/attachments/")
        assert same_response(listed, await self.api("get", "/drf/attachments/"))
        assert len(listed.data) == 2

    @override_settings(FILE_UPLOAD_MAX_MEMORY_SIZE=64)
    async def test_a_body_spooled_to_disk(self):
        response = await self.api(
            "post", "/aiodrf/attachments/", data=upload(b"x" * 4096), format="multipart"
        )
        assert response.status_code == 201, response.data
        stored = await Attachment.objects.aget(pk=response.data["id"])
        assert stored.file.size == 4096

    async def test_a_missing_file_is_a_validation_error(self):
        drf = await self.api(
            "post", "/drf/attachments/", data={"title": "x"}, format="multipart"
        )
        aiodrf = await self.api(
            "post", "/aiodrf/attachments/", data={"title": "x"}, format="multipart"
        )
        assert same_response(drf, aiodrf)
        assert aiodrf.status_code == 400


class UploadHopTests(MediaRoot, TestCase):
    async def test_parsing_and_saving_are_one_hop(self):
        with count_hops() as hops:
            response = await AsyncAPIClient().post(
                "/aiodrf/attachments/", data=upload(), format="multipart"
            )
        assert response.status_code == 201, response.data
        # The action's body reads ``request.data`` first: parsing the
        # multipart body (upload handlers write files), validation, the save
        # to storage and the representation share the hop.
        assert hops.calls == ["CreateModelMixin._create"]
