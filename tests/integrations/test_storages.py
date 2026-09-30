"""Real django-storages S3 backend; AWS calls terminate at botocore Stubber."""

import io
import threading
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from boto3.s3.transfer import TransferConfig
from botocore.exceptions import ClientError
from botocore.response import StreamingBody
from botocore.stub import Stubber
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils.asyncio import async_unsafe
from storages.backends.s3 import S3Storage

from aiodrf.test import AsyncAPIClient
from aiodrf.utils import run_sync
from tests.test_storage_contract import GuardedUpload
from tests.testapp.models import Attachment


@pytest.mark.aiodrf_settings(
    REPRESENTATION_MODE="thread", SERIALIZER_BACKEND_FALLBACK="drf"
)
@pytest.mark.parametrize("failure", [False, True])
async def test_presigned_url_refreshes_expired_credentials_in_worker(
    failure, monkeypatch
):
    import boto3
    import botocore.session
    from botocore.config import Config
    from botocore.credentials import RefreshableCredentials
    from botocore.httpsession import URLLib3Session
    from rest_framework import serializers

    from aiodrf import aio

    refreshed = []

    def no_network(*args, **kwargs):
        raise AssertionError("This credential/signing test must not contact AWS.")

    monkeypatch.setattr(URLLib3Session, "send", no_network)

    @async_unsafe("credential refresh on loop")
    def refresh():
        refreshed.append(threading.get_ident())
        if failure:
            raise RuntimeError("credential provider unavailable")
        return {
            "access_key": "renewed-key",
            "secret_key": "test-secret",
            "token": "renewed-token",
            "expiry_time": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        }

    def setup():
        session = botocore.session.get_session()
        # Test-only injection into the real signer; no IAM/profile/metadata lookup.
        session._credentials = RefreshableCredentials(
            access_key="expired-key",
            secret_key="test-secret",
            token="expired-token",
            expiry_time=datetime.now(UTC) - timedelta(seconds=1),
            refresh_using=refresh,
            method="test",
        )
        storage = S3Storage(
            bucket_name="aiodrf-test",
            region_name="us-east-1",
            client_config=Config(signature_version="s3v4"),
        )
        monkeypatch.setattr(
            storage, "_create_session", lambda: boto3.Session(botocore_session=session)
        )
        return storage, storage.connection.meta.client

    storage, client = await run_sync(setup)()

    class StoredFile:
        name = "test.txt"

        @property
        def url(self):
            return storage.url(self.name)

    class Item(serializers.Serializer):
        file = serializers.FileField()

    try:
        if failure:
            with pytest.raises(RuntimeError, match="credential provider unavailable"):
                await aio.data(Item({"file": StoredFile()}))
        else:
            for _ in range(2):
                data = await aio.data(Item({"file": StoredFile()}))
                query = parse_qs(urlsplit(data["file"]).query)
                assert query["X-Amz-Credential"][0].startswith("renewed-key/")
                assert query["X-Amz-Security-Token"] == ["renewed-token"]
        assert len(refreshed) == 1
        assert refreshed[0] != threading.get_ident()
    finally:
        await run_sync(client.close)()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("framework", ["drf", "aiodrf"])
@pytest.mark.parametrize("failure", [False, True])
async def test_s3_upload_url_read_delete_and_failure(
    framework, failure, monkeypatch, worker_connections
):
    calls = []

    def setup():
        storage = S3Storage(
            access_key="test-key",
            secret_key="test-secret",
            bucket_name="aiodrf-test",
            region_name="us-east-1",
            max_memory_size=1,
            transfer_config=TransferConfig(use_threads=False),
        )
        client = storage.connection.meta.client
        stubber = Stubber(client)
        if failure:
            stubber.add_client_error("put_object", "AccessDenied", http_status_code=403)
        else:
            stubber.add_response("put_object", {"ETag": '"test"'})
            stubber.add_response("head_object", {"ContentLength": 5})
            stubber.add_response("head_object", {"ContentLength": 5})
            stubber.add_response(
                "get_object",
                {"Body": StreamingBody(io.BytesIO(b"hello"), 5), "ContentLength": 5},
            )
            stubber.add_response("delete_object", {})
        stubber.activate()
        for name in ("save", "url", "open", "delete"):
            original = getattr(storage, name)

            @async_unsafe("S3 storage on the event loop")
            def guarded(*args, _method=original, _name=name, **kwargs):
                calls.append((_name, threading.get_ident()))
                return _method(*args, **kwargs)

            monkeypatch.setattr(storage, name, guarded)
        return storage, client, stubber

    storage, client, stubber = await run_sync(setup)()
    monkeypatch.setattr(Attachment._meta.get_field("file"), "storage", storage)
    monkeypatch.setattr(GuardedUpload, "paths", [])
    try:
        with override_settings(
            ROOT_URLCONF="tests.test_uploads",
            FILE_UPLOAD_MAX_MEMORY_SIZE=1,
            FILE_UPLOAD_HANDLERS=["tests.test_storage_contract.GuardedUpload"],
        ):
            payload = {"title": "S3", "file": SimpleUploadedFile("s3.txt", b"hello")}
            if failure:
                with pytest.raises(ClientError, match="AccessDenied"):
                    await AsyncAPIClient().post(
                        f"/{framework}/attachments/", payload, format="multipart"
                    )
                assert not await Attachment.objects.aexists()
            else:
                response = await AsyncAPIClient().post(
                    f"/{framework}/attachments/", payload, format="multipart"
                )
                assert response.status_code == 201, response.data
                assert (
                    "aiodrf-test.s3.amazonaws.com/attachments/s3.txt?"
                    in response.data["file"]
                )
                attachment = await Attachment.objects.aget()

                def read_and_delete():
                    with attachment.file.open("rb") as stored:
                        assert stored.read() == b"hello"
                        temporary = stored.file.file
                        assert not temporary.closed
                    assert temporary.closed
                    attachment.file.delete(save=False)

                await run_sync(read_and_delete)()
        assert GuardedUpload.paths
        assert all(not path.exists() for path in GuardedUpload.paths)
        assert [name for name, _ in calls] == (
            ["save"] if failure else ["save", "url", "open", "delete"]
        )
        assert all(thread != threading.get_ident() for _, thread in calls)
        stubber.assert_no_pending_responses()
    finally:
        stubber.deactivate()
        await run_sync(client.close)()
