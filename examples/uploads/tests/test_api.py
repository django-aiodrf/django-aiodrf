"""Api contracts for the uploads example."""

from django.test import override_settings


async def test_upload_writes_only_to_test_storage(client, tmp_path):
    with override_settings(MEDIA_ROOT=tmp_path):
        response = await client.post(
            "/upload/", files={"file": ("note.txt", b"hello", "text/plain")}
        )
        assert response.status_code == 201
        assert response.json()["size"] == 5
        assert (tmp_path / response.json()["name"]).read_bytes() == b"hello"
        assert (await client.post("/upload/", json={})).status_code == 415
