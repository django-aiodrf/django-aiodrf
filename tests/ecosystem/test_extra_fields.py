"""
drf-extra-fields: ``Base64ImageField`` decodes the upload and asks Pillow or
``filetype`` for its format in ``to_internal_value``, and
``PresentablePrimaryKeyRelatedField`` accepts a primary key but represents
the related object with a serializer, reading the relation in
``to_representation``. Both are synchronous DRF field code, so aiodrf has to
run them in its worker thread, never on the event loop.
"""

import asyncio
import base64
import io
import re

import pytest
from django.test import override_settings
from django.urls import path
from drf_extra_fields.fields import Base64ImageField
from drf_extra_fields.relations import PresentablePrimaryKeyRelatedField
from PIL import Image
from rest_framework import serializers
from rest_framework import viewsets as drf_viewsets
from rest_framework.permissions import AllowAny

from aiodrf import viewsets
from aiodrf.test import count_hops
from tests.base import both_transports
from tests.ecosystem.models import ExtraPhoto
from tests.testapp.models import Author
from tests.testapp.serializers import AuthorSerializer

# drf-extra-fields' image fields are not DRF's, so its serializers cannot be compiled:
# the tests run them on DRF's code whatever fallback the run's profile sets.
pytestmark = pytest.mark.aiodrf_settings(SERIALIZER_BACKEND_FALLBACK="drf")

# Whether a field ran with an event loop running in its thread.
on_loop = []


def loop_is_running():
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


class RecordingImageField(Base64ImageField):
    def to_internal_value(self, data):
        on_loop.append(("image", loop_is_running()))
        return super().to_internal_value(data)


class RecordingAuthorField(PresentablePrimaryKeyRelatedField):
    def to_representation(self, data):
        on_loop.append(("author", loop_is_running()))
        return super().to_representation(data)


class PhotoSerializer(serializers.ModelSerializer):
    image = RecordingImageField()
    author = RecordingAuthorField(
        queryset=Author.objects.all(), presentation_serializer=AuthorSerializer
    )

    class Meta:
        model = ExtraPhoto
        fields = ["id", "image", "author"]


class Policies:
    queryset = ExtraPhoto.objects.order_by("pk")
    serializer_class = PhotoSerializer
    authentication_classes = []
    permission_classes = [AllowAny]


class DRFPhotos(Policies, drf_viewsets.ModelViewSet):
    pass


class Photos(Policies, viewsets.ModelViewSet):
    pass


routes = {"get": "list", "post": "create"}
urlpatterns = [
    path("drf/", DRFPhotos.as_view(routes)),
    path("aiodrf/", Photos.as_view(routes)),
    path("aiodrf/<int:pk>/", Photos.as_view({"get": "retrieve"})),
]
media = override_settings(
    ROOT_URLCONF=__name__,
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    },
)


def png():
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def shape(data):
    # The file name is a fresh UUID for every upload.
    return {
        **data,
        "id": None,
        "image": re.sub(r"[0-9a-f-]{36}", "<uuid>", data["image"]),
    }


@both_transports
class _ExtraFieldsTests:
    def setUp(self):
        super().setUp()
        on_loop.clear()

    @media
    async def test_a_base64_image_and_a_presented_relation(self):
        author = await Author.objects.acreate(name="Ursula")
        payload = {"image": png(), "author": author.pk}
        drf = await self.api("post", "/drf/", data=payload)
        on_loop.clear()
        with count_hops() as hops:
            aiodrf = await self.api("post", "/aiodrf/", data=payload)
        assert aiodrf.status_code == drf.status_code == 201, aiodrf.data
        assert shape(aiodrf.data) == shape(drf.data)
        assert aiodrf.data["author"] == {"id": author.pk, "name": "Ursula"}
        assert aiodrf.data["image"].endswith(".png")
        assert on_loop == [("image", False), ("author", False)]
        if self.transport == "asgi":
            assert hops.calls == ["CreateModelMixin._create"]

        drf_listed = await self.api("get", "/drf/")
        on_loop.clear()
        listed = await self.api("get", "/aiodrf/")
        retrieved = await self.api("get", f"/aiodrf/{aiodrf.data['id']}/")
        assert [shape(item) for item in listed.data] == [
            shape(item) for item in drf_listed.data
        ]
        assert retrieved.data == aiodrf.data
        # Two photos listed (the DRF view created one), one retrieved.
        assert on_loop == [("author", False)] * 3

    @media
    async def test_invalid_images_are_drfs_400(self):
        author = await Author.objects.acreate(name="Ursula")
        for image in ("not base64!", base64.b64encode(b"plain text").decode(), 12):
            with self.subTest(image=image):
                payload = {"image": image, "author": author.pk}
                drf = await self.api("post", "/drf/", data=payload)
                aiodrf = await self.api("post", "/aiodrf/", data=payload)
                assert aiodrf.status_code == drf.status_code == 400
                assert aiodrf.data == drf.data
        payload = {"image": png(), "author": author.pk + 100}
        drf = await self.api("post", "/drf/", data=payload)
        aiodrf = await self.api("post", "/aiodrf/", data=payload)
        assert aiodrf.status_code == drf.status_code == 400
        assert aiodrf.data == drf.data
