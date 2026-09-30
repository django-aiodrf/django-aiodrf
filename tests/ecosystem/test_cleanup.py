"""
django-cleanup deletes the old file of a FileField when the row is updated or
deleted, from ``transaction.on_commit``. aiodrf saves in a worker thread, in
one transaction (``ATOMIC_SAVE``): the storage deletion has to run there,
after the commit, and not at all when the save is rolled back.

``TransactionTestCase``: ``TestCase``'s transaction never commits.
"""

import tempfile
import threading
from pathlib import Path

from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TransactionTestCase, override_settings
from django.urls import include, path
from django.utils.asyncio import async_unsafe
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.routers import SimpleRouter

from aiodrf import viewsets
from aiodrf.test import AsyncAPIClient, count_hops
from tests.base import AsyncTransport, SyncTransport
from tests.ecosystem.models import CleanedFile
from tests.testapp.models import Attachment


class GuardedStorage(FileSystemStorage):
    """Deleting on the event loop raises ``SynchronousOnlyOperation``."""

    deletions = []

    @async_unsafe("django-cleanup deleting a file on the event loop")
    def delete(self, name):
        self.deletions.append((name, threading.get_ident()))
        super().delete(name)


class CleanedFileSerializer(serializers.ModelSerializer):
    class Meta:
        model = CleanedFile
        fields = ["id", "title", "file"]

    def update(self, instance, validated_data):
        instance = super().update(instance, validated_data)
        if instance.title == "Veto":
            raise serializers.ValidationError({"title": "Vetoed after saving."})
        return instance


class AttachmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Attachment
        fields = ["id", "title", "file"]


class Policies:
    authentication_classes = []
    permission_classes = [AllowAny]


class CleanedFiles(Policies, viewsets.ModelViewSet):
    queryset = CleanedFile.objects.all()
    serializer_class = CleanedFileSerializer


class Attachments(Policies, viewsets.ModelViewSet):
    # Not selected for django-cleanup: the plain view of the hop test.
    queryset = Attachment.objects.all()
    serializer_class = AttachmentSerializer


router = SimpleRouter()
router.register("files", CleanedFiles)
router.register("attachments", Attachments)
urlpatterns = [path("", include(router.urls))]


def upload(name, title="Notes"):
    return {"title": title, "file": SimpleUploadedFile(name, name.encode())}


class GuardedMedia:
    def setUp(self):
        super().setUp()
        media = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(override_settings(ROOT_URLCONF=__name__, MEDIA_ROOT=media))
        self.media = Path(media)
        storage = GuardedStorage(location=media)
        for model in (CleanedFile, Attachment):
            field = model._meta.get_field("file")
            self.addCleanup(setattr, field, "storage", field.storage)
            field.storage = storage
        GuardedStorage.deletions = []

    def stored(self):
        return sorted(
            str(file.relative_to(self.media)) for file in self.media.rglob("*.txt")
        )


class _CleanupTests(GuardedMedia):
    async def create(self):
        created = await self.api(
            "post", "/files/", data=upload("old.txt"), format="multipart"
        )
        assert created.status_code == 201, created.data
        return f"/files/{created.data['id']}/"

    def assert_deleted_off_the_loop(self, names):
        assert [name for name, _ in GuardedStorage.deletions] == names
        loop_thread = threading.get_ident()
        assert all(thread != loop_thread for _, thread in GuardedStorage.deletions)

    async def test_an_update_deletes_the_replaced_file(self):
        url = await self.create()
        response = await self.api(
            "patch", url, data=upload("new.txt"), format="multipart"
        )
        assert response.status_code == 200, response.data
        assert self.stored() == ["cleanup/new.txt"]
        self.assert_deleted_off_the_loop(["cleanup/old.txt"])

    async def test_a_destroy_deletes_the_file(self):
        url = await self.create()
        assert (await self.api("delete", url)).status_code == 204
        assert self.stored() == []
        self.assert_deleted_off_the_loop(["cleanup/old.txt"])

    async def test_a_rolled_back_update_keeps_the_old_file(self):
        url = await self.create()
        response = await self.api(
            "patch", url, data=upload("new.txt", title="Veto"), format="multipart"
        )
        assert response.status_code == 400, response.data
        assert (await CleanedFile.objects.aget()).file.name == "cleanup/old.txt"
        # The deletion waited for a commit that never came. The new file was
        # written to storage before the rollback and stays (django-cleanup
        # deletes replaced files, not ones of a failed save).
        assert GuardedStorage.deletions == []
        assert self.stored() == ["cleanup/new.txt", "cleanup/old.txt"]


class CleanupASGITests(AsyncTransport, _CleanupTests, TransactionTestCase):
    pass


class CleanupWSGITests(SyncTransport, _CleanupTests, TransactionTestCase):
    pass


class CleanupHopTests(GuardedMedia, TransactionTestCase):
    async def test_cleanup_adds_no_hop(self):
        client = AsyncAPIClient()
        measured = {}
        for prefix in ("attachments", "files"):
            with count_hops() as hops:
                created = await client.post(
                    f"/{prefix}/", upload("old.txt"), format="multipart"
                )
            measured[prefix, "post"] = hops.count
            url = f"/{prefix}/{created.data['id']}/"
            for method, data in [("patch", upload("new.txt")), ("delete", None)]:
                with count_hops() as hops:
                    response = await getattr(client, method)(
                        url, data, format="multipart"
                    )
                assert response.status_code < 300, response.data
                measured[prefix, method] = hops.count
        # Two deletions, one for each request of the selected model.
        assert len(GuardedStorage.deletions) == 2
        # One hop each: the deletion runs on commit, inside the hop that saved.
        assert set(measured.values()) == {1}, measured
