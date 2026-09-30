"""Concurrent HTTP requests must not share the selected database schema."""

import asyncio

import pytest
from customers.models import Customer, Domain
from django.db import connection, connections
from django_tenants.utils import schema_context
from notes.models import Note

from aiodrf.utils import run_sync


@pytest.fixture
async def tenants(transactional_db):
    def create():
        connection.set_schema_to_public()
        created = []
        for name in ("alpha", "beta"):
            customer = Customer(schema_name=name, name=name)
            customer.save(verbosity=0)
            Domain.objects.create(
                tenant=customer, domain=f"{name}.example.test", is_primary=True
            )
            with schema_context(name):
                Note.objects.create(text=name)
            created.append(customer)
        connection.set_schema_to_public()
        return created

    created = await run_sync(create)()
    try:
        yield
    finally:

        def drop_created_schemas():
            connection.set_schema_to_public()
            for tenant in created:
                tenant.delete(force_drop=True)
            # The setup/teardown worker is outside an HTTP request: it owns
            # its connection and must close it before Django drops the test DB.
            connections.close_all()

        await run_sync(drop_created_schemas)()


async def test_tenant_isolation_through_django_asgi(client, tenants):
    responses = await asyncio.gather(
        *[
            client.get(f"http://{name}.example.test/texts/")
            for name in ("alpha", "beta", "alpha", "beta")
        ]
    )
    assert [r.json() for r in responses] == [
        {"tenant": name, "texts": [name]} for name in ("alpha", "beta", "alpha", "beta")
    ]
    created = await client.post(
        "http://alpha.example.test/notes/", json={"text": "alpha-only"}
    )
    assert created.status_code == 201
    pk = created.json()["id"]
    assert (
        await client.get(f"http://beta.example.test/notes/{pk}/")
    ).status_code == 404
    assert (await client.get("http://unknown.example.test/notes/")).status_code == 404
