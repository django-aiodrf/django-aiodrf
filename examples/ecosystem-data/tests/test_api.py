"""Round-trip vendor values and wire formats through the actual ASGI stack."""

import base64
import io

import pytest
from demo.models import ArchivedNote, Author, Book, Limits, Photo
from PIL import Image

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
async def book():
    author = await Author.objects.acreate(name="Ursula", phone="+442079460958")
    return await Book.objects.acreate(
        title="Earthsea", author=author, limits=Limits(rate=10)
    )


async def test_money_phone_tags_and_pydantic_fields_round_trip(client):
    author = await client.post(
        "/authors/", json={"name": "Ursula", "phone": "+44 20 7946 0958"}
    )
    assert author.status_code == 201
    assert author.json()["phone"] == "+442079460958"
    payload = {
        "title": "Earthsea",
        "author": author.json()["id"],
        "price": "3.50",
        "price_currency": "USD",
        "tags": ["sea", "wizard"],
        "limits": {"rate": 10},
    }
    response = await client.post("/books/", json=payload)
    assert response.status_code == 201, response.text
    assert response.json()["price"] == "3.50"
    assert response.json()["limits"] == {"rate": 10, "burst": 1}
    assert sorted(response.json()["tags"]) == ["sea", "wizard"]
    invalid = await client.post("/books/", json={**payload, "limits": {"rate": -1}})
    assert invalid.status_code == 400
    assert invalid.json()["type"] == "validation_error"


async def test_rest_filters_validates_parameters_and_filters_detail(client, book):
    response = await client.get(
        "/filtered-books/", params={"title": "earth", "author": "ursula"}
    )
    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [book.pk]
    assert (
        await client.get("/filtered-books/", params={"title": "a"})
    ).status_code == 400
    assert (
        await client.get("/filtered-books/", params={"unknown": "value"})
    ).status_code == 400
    assert (
        await client.get(f"/filtered-books/{book.pk}/", params={"title": "absent"})
    ).status_code == 404
    assert len((await client.get("/filtered-books/")).json()) == 1


async def test_nested_write_and_expansion(client):
    created = await client.post(
        "/nested-books/", json={"title": "Earthsea", "author": {"name": "Ursula"}}
    )
    assert created.status_code == 201, created.text
    pk = created.json()["id"]
    expanded = await client.get(f"/expanded-books/{pk}/?expand=author&fields=id,author")
    assert set(expanded.json()) == {"id", "author"}
    assert expanded.json()["author"]["name"] == "Ursula"
    author_pk = created.json()["author"]["id"]
    assert len((await client.get(f"/authors/{author_pk}/books/")).json()) == 1
    assert (await client.get("/authors/9999/books/")).json() == []


async def test_polymorphic_and_soft_delete(client):
    created = await client.post(
        "/projects/",
        json={"resourcetype": "ArtProject", "topic": "Painting", "artist": "Frida"},
    )
    assert created.status_code == 201, created.text
    assert (await client.get("/projects/")).json()[0]["artist"] == "Frida"
    note = await client.post("/notes/", json={"text": "Retained"})
    assert (await client.delete(f"/notes/{note.json()['id']}/")).status_code == 204
    assert await ArchivedNote.objects.acount() == 0
    assert await ArchivedNote.all_objects.acount() == 1


async def test_dataclass_and_nested_multipart(client):
    response = await client.post(
        "/address/", json={"city": "Portland", "postal_code": "97201"}
    )
    assert response.json() == {"city": "Portland", "postal_code": "97201"}
    response = await client.post(
        "/multipart/",
        data={"author.name": "Ursula"},
        files={"attachment": ("notes.txt", b"Local example", "text/plain")},
    )
    assert response.status_code == 200, response.text
    assert response.json()["filename"] == "notes.txt"
    assert response.json()["author"]["name"] == "Ursula"


async def test_base64_upload_and_cleanup(client, settings, tmp_path, book):
    settings.MEDIA_ROOT = tmp_path
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "blue").save(buffer, format="PNG")
    image = base64.b64encode(buffer.getvalue()).decode()
    response = await client.post(
        "/photos/", json={"author": book.author_id, "image": image}
    )
    assert response.status_code == 201, response.text
    assert response.json()["author"]["name"] == "Ursula"
    photo = await Photo.objects.aget(pk=response.json()["id"])
    assert (tmp_path / photo.image.name).is_file()
    assert (await client.delete(f"/photos/{photo.pk}/")).status_code == 204
    assert not (tmp_path / photo.image.name).exists()


async def test_renderers_and_pagination(client, book):
    response = await client.post("/camel-case/", json={"displayName": "Ursula"})
    assert response.json() == {"displayName": "Ursula"}
    assert (await client.get("/orjson/")).json()[0]["author_name"] == "Ursula"
    spreadsheet = await client.get("/spreadsheet/")
    assert spreadsheet.status_code == 200, spreadsheet.text
    assert spreadsheet.content.startswith(b"PK")
    assert (
        spreadsheet.headers["content-disposition"] == "attachment; filename=books.xlsx"
    )
    page = await client.get("/uncounted/")
    assert "count" not in page.json()
    assert page.json()["results"][0]["title"] == "Earthsea"
    document = await client.get(
        "/jsonapi/books/", headers={"Accept": "application/vnd.api+json"}
    )
    assert document.status_code == 200, document.text
    assert document.json()["data"][0]["attributes"]["title"] == "Earthsea"
    table = await client.get(
        "/table/",
        params={"draw": 1, "columns[0][data]": "title", "start": 0, "length": 10},
    )
    assert table.status_code == 200, table.text
    assert table.json()["recordsTotal"] == 1
    html = await client.get("/books/", headers={"Accept": "text/html"})
    assert html.status_code == 200, html.text
    assert "/static/rest_wind/css/styles.css" in html.text
