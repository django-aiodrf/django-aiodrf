"""An actual codemod result, not a separately handwritten migrated example."""

import inspect
import types

import pytest
from django.test import override_settings
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.validation import validate_schema

from aiodrf.codemod import transform_source
from aiodrf.test import AsyncAPIClient
from aiodrf.utils import run_sync
from tests import migration_app
from tests.testapp.models import Author


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("origin", ["drf", "adrf"])
@pytest.mark.filterwarnings(
    "ignore:'asyncio.iscoroutinefunction' is deprecated.*:DeprecationWarning:async_property.base"
)
async def test_migrated_drf_application_preserves_http_and_schema_contracts(
    origin, worker_connections
):
    original = migration_app
    if origin == "adrf":
        pytest.importorskip("adrf")
        from tests import migration_adrf

        original = migration_adrf
    result = transform_source(inspect.getsource(original))
    assert result.notes == []
    assert transform_source(result.code).code == result.code
    converted = types.ModuleType("tests.migrated_app")
    exec(compile(result.code, "migrated_app.py", "exec"), vars(converted))  # noqa: S102 -- trusted fixture
    observations = []
    for app in (original, converted):
        await Author.objects.all().adelete()
        with override_settings(ROOT_URLCONF=app):
            client = AsyncAPIClient()
            created = await client.post(
                "/authors/", {"name": " Ursula "}, format="json"
            )
            assert created.status_code == 201
            pk = created.data["id"]
            updated = await client.patch(
                f"/authors/{pk}/", {"name": "Octavia"}, format="json"
            )
            filtered = await client.get("/authors/?name=Octavia")
            action = await client.get("/authors/selected/")
            denied = await client.get("/authors/denied/")
            forbidden = await client.get("/authors/", HTTP_X_DENY="1")
            invalid = await client.post("/authors/", {"name": ""}, format="json")
            schema = await run_sync(SchemaGenerator().get_schema)(
                request=None, public=True
            )
            validate_schema(schema)
            observations.append(
                (
                    updated.status_code,
                    updated.data["name"],
                    [row["name"] for row in filtered.data],
                    action.data,
                    denied.status_code,
                    denied.data,
                    forbidden.status_code,
                    invalid.status_code,
                    invalid.data,
                    set(schema["paths"]),
                )
            )
    assert observations[0] == observations[1]
