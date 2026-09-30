"""Upload parsing and storage hooks stay inside Django's sync boundary."""

from pathlib import Path

import pytest
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.files.uploadhandler import StopUpload, TemporaryFileUploadHandler
from django.test import override_settings
from django.utils.asyncio import async_unsafe

from aiodrf.test import AsyncAPIClient
from aiodrf.utils import run_sync
from tests.testapp.models import Attachment


class GuardedStorage(FileSystemStorage):
    fail_save = False

    @async_unsafe("storage constructor on loop")
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    @async_unsafe("storage open on loop")
    def _open(self, *args, **kwargs):
        return super()._open(*args, **kwargs)

    @async_unsafe("storage save on loop")
    def _save(self, *args, **kwargs):
        if self.fail_save:
            raise OSError("storage unavailable")
        return super()._save(*args, **kwargs)

    @async_unsafe("storage delete on loop")
    def delete(self, name):
        return super().delete(name)


class GuardedUpload(TemporaryFileUploadHandler):
    paths = []
    abort = False

    @async_unsafe("upload new_file on loop")
    def new_file(self, *args, **kwargs):
        super().new_file(*args, **kwargs)
        self.paths.append(Path(self.file.temporary_file_path()))

    @async_unsafe("upload write on loop")
    def receive_data_chunk(self, raw_data, start):
        if self.abort:
            raise StopUpload(connection_reset=False)
        return super().receive_data_chunk(raw_data, start)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("failure", [None, "storage", "upload"])
async def test_upload_storage_failures_and_temporary_file_cleanup(
    tmp_path, monkeypatch, failure, worker_connections
):
    monkeypatch.setattr(GuardedStorage, "fail_save", failure == "storage")
    monkeypatch.setattr(GuardedUpload, "abort", failure == "upload")
    monkeypatch.setattr(GuardedUpload, "paths", [])
    with override_settings(
        ROOT_URLCONF="tests.test_uploads",
        FILE_UPLOAD_HANDLERS=[f"{__name__}.GuardedUpload"],
        FILE_UPLOAD_MAX_MEMORY_SIZE=1,
        STORAGES={
            "default": {
                "BACKEND": f"{__name__}.GuardedStorage",
                "OPTIONS": {"location": tmp_path},
            }
        },
    ):
        payload = {
            "title": "guarded",
            "file": SimpleUploadedFile("large.txt", b"x" * 131072),
        }
        if failure == "storage":
            with pytest.raises(OSError, match="storage unavailable"):
                await AsyncAPIClient().post(
                    "/aiodrf/attachments/", payload, format="multipart"
                )
        else:
            response = await AsyncAPIClient().post(
                "/aiodrf/attachments/", payload, format="multipart"
            )
            assert response.status_code == (400 if failure else 201)
        assert GuardedUpload.paths
        assert not any(path.exists() for path in GuardedUpload.paths)
        if failure:
            assert not await Attachment.objects.aexists()
        else:
            attachment = await Attachment.objects.aget()

            def read_and_delete():
                with attachment.file.open("rb") as stored:
                    assert len(stored.read()) == 131072
                attachment.file.delete(save=False)

            await run_sync(read_and_delete)()
            assert not list(tmp_path.rglob("*.txt"))
