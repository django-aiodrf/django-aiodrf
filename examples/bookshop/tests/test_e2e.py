"""
The example project end to end: requests go through the real ASGI
application (``bookshop.asgi``'s ``get_asgi_application``) with its lifespan,
over HTTPX, as a server would send them.

The stock service is replaced by an ``httpx.MockTransport``: the test passes
its own lifespan factory, as the lifespan guide recommends, and the view code
is unchanged.
"""

import json
from contextlib import asynccontextmanager

import httpx
import pytest
from asgi_lifespan import LifespanManager
from django.contrib.auth.models import User
from django.core.cache import cache
from rest_framework.authtoken.models import Token

from aiodrf.asgi import get_asgi_application
from bookshop.lifecycle import Resources
from catalog.models import Author, Book

pytestmark = pytest.mark.django_db(transaction=True)


class StockService:
    """Answers ``POST /levels/`` with a level for each SKU, and counts calls."""

    def __init__(self):
        self.requests = []

    def __call__(self, request):
        skus = json.loads(request.content)
        self.requests.append(skus)
        return httpx.Response(200, json={sku: len(sku) for sku in skus})


@pytest.fixture
def stock():
    return StockService()


@pytest.fixture
async def client(stock):
    # Throttle counters live in the cache; every test starts without any.
    await cache.aclear()

    @asynccontextmanager
    async def lifespan():
        transport = httpx.MockTransport(stock)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://stock"
        ) as http:
            yield Resources(stock=http)

    application = get_asgi_application(lifespan=lifespan)
    async with LifespanManager(application) as manager:
        transport = httpx.ASGITransport(app=manager.app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as http:
            yield http


@pytest.fixture
async def books():
    le_guin = await Author.objects.acreate(name="Ursula K. Le Guin")
    lem = await Author.objects.acreate(name="Stanisław Lem")
    return [
        await Book.objects.acreate(
            sku="EARTHSEA", title="A Wizard of Earthsea", author=le_guin
        ),
        await Book.objects.acreate(
            sku="DISPOSSESSED", title="The Dispossessed", author=le_guin
        ),
        await Book.objects.acreate(sku="SOLARIS", title="Solaris", author=lem),
    ]


@pytest.fixture
async def token():
    user = await User.objects.acreate_user("editor", password="unused")
    return (await Token.objects.acreate(user=user)).key


async def test_the_list_asks_the_stock_service_once_per_page(client, stock, books):
    response = await client.get("/books/")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 3
    assert [
        (book["sku"], book["author"], book["in_stock"]) for book in body["results"]
    ] == [
        ("EARTHSEA", "Ursula K. Le Guin", 8),
        ("DISPOSSESSED", "Ursula K. Le Guin", 12),
        ("SOLARIS", "Stanisław Lem", 7),
    ]
    assert stock.requests == [["EARTHSEA", "DISPOSSESSED", "SOLARIS"]]


async def test_filtering_and_pagination(client, stock, books):
    response = await client.get("/books/", params={"author__name": "Stanisław Lem"})
    assert [book["sku"] for book in response.json()["results"]] == ["SOLARIS"]
    assert stock.requests == [["SOLARIS"]]


async def test_writes_need_a_token(client, books, token):
    book = {
        "sku": "LATHE",
        "title": "The Lathe of Heaven",
        "author": "Ursula K. Le Guin",
    }
    assert (await client.post("/books/", json=book)).status_code == 401
    response = await client.post(
        "/books/", json=book, headers={"Authorization": f"Token {token}"}
    )
    assert response.status_code == 201, response.text
    assert response.json()["in_stock"] is None
    assert await Book.objects.filter(sku="LATHE").aexists()


async def test_validation_errors_are_drfs(client, books, token):
    book = {"sku": "EARTHSEA", "title": "Again", "author": "Nobody"}
    response = await client.post(
        "/books/", json=book, headers={"Authorization": f"Token {token}"}
    )
    assert response.status_code == 400
    assert set(response.json()) == {"sku", "author"}


async def test_an_unchanged_book_is_not_sent_again(client, books, token):
    first = await client.get(f"/books/{books[0].pk}/")
    etag = first.headers["ETag"]
    again = await client.get(f"/books/{books[0].pk}/", headers={"If-None-Match": etag})
    assert again.status_code == 304
    assert again.content == b""

    # A write with a stale ETag is refused before the handler runs.
    auth = {"Authorization": f"Token {token}"}
    changed = await client.patch(
        f"/books/{books[0].pk}/", json={"title": "Earthsea"}, headers=auth
    )
    assert changed.status_code == 200
    stale = await client.patch(
        f"/books/{books[0].pk}/",
        json={"title": "Stale"},
        headers={**auth, "If-Match": etag},
    )
    assert stale.status_code == 412
    assert (await Book.objects.aget(pk=books[0].pk)).title == "Earthsea"


async def test_search_validates_its_query_string(client, books):
    response = await client.get("/search/", params={"q": "the", "limit": 1})
    assert response.json() == {"titles": ["The Dispossessed"]}
    invalid = await client.get("/search/", params={"q": "x", "limit": 0})
    assert invalid.status_code == 400
    assert set(invalid.json()) == {"q", "limit"}


async def test_the_export_streams_every_book(client, books):
    async with client.stream("GET", "/books/export/") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/x-ndjson")
        lines = [json.loads(line) async for line in response.aiter_lines() if line]
    assert [line["sku"] for line in lines] == ["EARTHSEA", "DISPOSSESSED", "SOLARIS"]


async def test_anonymous_clients_are_throttled(client, books):
    # DRF reads the rates once, at import: this is the configured 60/min.
    codes = [
        (await client.get("/search/", params={"q": "Solaris"})).status_code
        for _ in range(61)
    ]
    assert codes == [200] * 60 + [429]


async def test_the_schema_documents_the_api(client):
    response = await client.get("/schema/", headers={"Accept": "application/json"})
    assert response.status_code == 200
    schema = response.json()
    assert {"/books/", "/books/{id}/", "/books/export/", "/search/"} <= set(
        schema["paths"]
    )
    parameters = {p["name"] for p in schema["paths"]["/search/"]["get"]["parameters"]}
    assert parameters == {"q", "limit"}


async def test_the_schema_of_the_export_is_what_it_streams(client, books):
    schema = (
        await client.get("/schema/", headers={"Accept": "application/json"})
    ).json()
    export = schema["paths"]["/books/export/"]["get"]
    content = export["responses"]["200"]["content"]
    assert set(content) == {"application/x-ndjson"}
    stream = content["application/x-ndjson"]["schema"]["$ref"].rsplit("/", 1)[-1]
    item = schema["components"]["schemas"][stream]["x-aiodrf-item-schema"][
        "$ref"
    ].rsplit("/")[-1]
    documented = set(schema["components"]["schemas"][item]["properties"])
    # The export reads every book: it takes no filter or page parameters.
    assert export.get("parameters", []) == []
    async with client.stream("GET", "/books/export/") as response:
        lines = [json.loads(line) async for line in response.aiter_lines() if line]
    assert {key for line in lines for key in line} == documented
