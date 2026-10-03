"""Api contracts for the serializer backends example."""

import pytest
from demo.models import Article, Tag
from django.test import override_settings
from project.settings import PROFILES

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("profile", list(PROFILES))
async def test_profiles_preserve_this_models_output(client, profile):
    tag = await Tag.objects.acreate(name=profile)
    with override_settings(FASTDRF=PROFILES[profile]):
        created = await client.post(
            "/articles/", json={"title": "Article", "tags": [tag.pk]}
        )
        assert created.status_code == 201
        expected = {
            "id": created.json()["id"],
            "title": "Article",
            "tags": [{"id": tag.pk, "name": profile}],
        }
        assert created.json() == expected
        assert (await client.get(f"/articles/{expected['id']}/")).json() == expected
        assert (
            await client.get(f"/articles/{expected['id']}/explicit_backend/")
        ).json() == expected
        bad = await client.post("/articles/", json={"title": "Bad", "tags": [999999]})
        assert bad.status_code == 400
        assert not await Article.objects.filter(title="Bad").aexists()


async def test_view_scoped_copy_plan_without_global_tuning(client):
    with override_settings(FASTDRF=PROFILES["normal"]):
        response = await client.post(
            "/selected-articles/", json={"title": "Scoped", "tags": []}
        )
        assert response.status_code == 201
        detail = await client.get(f"/selected-articles/{response.json()['id']}/")
        assert detail.json() == response.json()
