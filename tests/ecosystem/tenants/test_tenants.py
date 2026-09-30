"""
django-tenants selects a PostgreSQL schema per request: its middleware calls
``connection.set_tenant()``, which is state of the calling thread's database
connection. aiodrf queries in its worker thread (``run_sync``, thread
sensitive) and Django's async ORM runs in the same thread, so both have to
see the schema the middleware chose for their request.
"""

import asyncio
import json

import pytest
from asgiref.sync import sync_to_async
from django.core.asgi import get_asgi_application
from django.db import connection
from django.test import TestCase
from django.urls import include, path
from django_tenants.utils import schema_context
from rest_framework import generics as drf_generics
from rest_framework import serializers
from rest_framework import views as drf_views
from rest_framework.response import Response as DRFResponse
from rest_framework.routers import SimpleRouter

from aiodrf import viewsets
from aiodrf.response import Response
from aiodrf.test import AsyncAPIClient, count_hops
from aiodrf.views import APIView
from tests.asgi_driver import ASGIDriver, http_scope
from tests.base import both_transports
from tests.ecosystem.tenants.customers.models import Customer, Domain
from tests.ecosystem.tenants.notes.models import Note

NOTES = {"alpha": ["a1", "a2"], "beta": ["b1"]}


class NoteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Note
        fields = ["id", "text"]


class DRFNotes(drf_generics.ListCreateAPIView):
    queryset = Note.objects.all()
    serializer_class = NoteSerializer


class DRFNote(drf_generics.RetrieveAPIView):
    queryset = Note.objects.all()
    serializer_class = NoteSerializer


class Notes(viewsets.ModelViewSet):
    queryset = Note.objects.all()
    serializer_class = NoteSerializer


class DRFTexts(drf_views.APIView):
    def get(self, request):
        texts = [note.text for note in Note.objects.all()]
        return DRFResponse({"tenant": request.tenant.schema_name, "texts": texts})


class Texts(APIView):
    async def get(self, request):
        # Django's async ORM: its own thread-sensitive hop, the request's thread.
        texts = [note.text async for note in Note.objects.all()]
        return Response({"tenant": request.tenant.schema_name, "texts": texts})


router = SimpleRouter()
router.register("notes", Notes)
urlpatterns = [
    path("drf/notes/", DRFNotes.as_view()),
    path("drf/notes/<int:pk>/", DRFNote.as_view()),
    path("aiodrf/", include(router.urls)),
    path("drf/texts/", DRFTexts.as_view()),
    path("aiodrf/texts/", Texts.as_view()),
]


def host(schema):
    return f"{schema}.example.test"


@pytest.fixture(scope="module", autouse=True)
def tenants(django_db_setup, django_db_blocker):
    """Two tenants with their rows, committed, so that every thread sees them."""
    with django_db_blocker.unblock():
        customers = []
        for schema, texts in NOTES.items():
            customer = Customer(schema_name=schema, name=schema.title())
            customer.save(verbosity=0)
            Domain.objects.create(tenant=customer, domain=host(schema), is_primary=True)
            with schema_context(schema):
                Note.objects.bulk_create(Note(text=text) for text in texts)
            customers.append(customer)
        connection.set_schema_to_public()
        yield
        connection.set_schema_to_public()
        for customer in customers:
            customer.delete(force_drop=True)


class HostAsyncAPIClient(AsyncAPIClient):
    """
    Sends one ``host`` header. Django's async request factory always sends
    ``host: testserver`` and adds a given one after it, which Django joins
    into ``testserver,alpha.example.test``, an invalid host.
    """

    def __init__(self, host, **defaults):
        super().__init__(**defaults)
        self.host = host.encode()

    def request(self, **request):
        request["headers"] = [
            *(header for header in request["headers"] if header[0] != b"host"),
            (b"host", self.host),
        ]
        return super().request(**request)


class OnTenant:
    async def on(self, schema, method, url, **kwargs):
        if self.transport == "asgi":
            return await getattr(HostAsyncAPIClient(host(schema)), method)(
                url, **kwargs
            )
        return await self.api(method, url, headers={"host": host(schema)}, **kwargs)


def texts(response):
    return [note["text"] for note in response.data]


@both_transports
class _TenantTests(OnTenant):
    async def test_each_domain_lists_its_own_rows(self):
        for schema, expected in NOTES.items():
            for prefix in ("drf", "aiodrf"):
                with self.subTest(schema=schema, prefix=prefix):
                    response = await self.on(schema, "get", f"/{prefix}/notes/")
                    assert response.status_code == 200
                    assert texts(response) == expected

    async def test_a_row_of_another_tenant_is_not_found(self):
        # Primary keys start at 1 in each schema; only alpha has a second note.
        for prefix in ("drf", "aiodrf"):
            with self.subTest(prefix=prefix):
                found = await self.on("alpha", "get", f"/{prefix}/notes/2/")
                missing = await self.on("beta", "get", f"/{prefix}/notes/2/")
                assert (found.status_code, found.data["text"]) == (200, "a2")
                assert missing.status_code == 404

    async def test_the_async_orm_in_a_handler_uses_the_tenant(self):
        for schema, expected in NOTES.items():
            for prefix in ("drf", "aiodrf"):
                with self.subTest(schema=schema, prefix=prefix):
                    response = await self.on(schema, "get", f"/{prefix}/texts/")
                    assert response.data == {"tenant": schema, "texts": expected}

    async def test_a_create_is_saved_in_the_tenants_schema(self):
        for prefix in ("drf", "aiodrf"):
            with self.subTest(prefix=prefix):
                response = await self.on(
                    "beta", "post", f"/{prefix}/notes/", data={"text": prefix}
                )
                assert response.status_code == 201

        def texts_in(schema):
            with schema_context(schema):
                return list(Note.objects.values_list("text", flat=True))

        assert await sync_to_async(texts_in)("beta") == ["b1", "drf", "aiodrf"]
        assert await sync_to_async(texts_in)("alpha") == ["a1", "a2"]

    async def test_an_unknown_domain_is_a_404(self):
        for prefix in ("drf", "aiodrf"):
            with self.subTest(prefix=prefix):
                response = await self.on("none", "get", f"/{prefix}/notes/")
                assert response.status_code == 404


class HopTests(TestCase):
    async def test_the_tenant_middleware_adds_no_aiodrf_hop(self):
        with count_hops() as hops:
            response = await HostAsyncAPIClient(host("alpha")).get("/aiodrf/notes/")
        assert texts(response) == NOTES["alpha"]
        assert hops.count == 1, hops.calls


async def through_asgi(application, schema, url):
    scope = http_scope(url)
    scope["headers"] = [(b"host", host(schema).encode())]
    async with ASGIDriver(application, scope) as driver:
        await driver.incoming.put({"type": "http.request", "body": b""})
        await driver.finish()
    start, *body = driver.sent
    assert start["status"] == 200
    return json.loads(b"".join(message.get("body", b"") for message in body))


@pytest.mark.django_db
async def test_concurrent_requests_for_different_tenants_through_the_asgi_handler():
    # Django's ASGI handler gives each request a thread of its own for
    # thread-sensitive work (``ThreadSensitiveContext``), so each request's
    # middleware, aiodrf worker hops and async ORM share that thread's
    # connection and schema, and requests do not share one.
    application = get_asgi_application()
    calls = [
        (schema, url)
        for _ in range(4)
        for schema in NOTES
        for url in ("/aiodrf/notes/", "/aiodrf/texts/")
    ]
    bodies = await asyncio.gather(
        *(through_asgi(application, schema, url) for schema, url in calls)
    )
    for (schema, url), body in zip(calls, bodies, strict=True):
        if url == "/aiodrf/texts/":
            assert body == {"tenant": schema, "texts": NOTES[schema]}
        else:
            assert [note["text"] for note in body] == NOTES[schema]
