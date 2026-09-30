import pytest
from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.authtoken.models import Token

from aiodrf.test import count_hops
from tests.base import both_transports
from tests.testapp.models import Author, Book, Tag


class Fixtures:
    @classmethod
    def setUpTestData(cls):
        cls.author = Author.objects.create(name="Ursula")
        cls.other = Author.objects.create(name="Iain")
        cls.tag = Tag.objects.create(name="sf")
        cls.books = [
            Book.objects.create(
                title=f"Book {i}", isbn=f"{i:013d}", pages=100 + i, author=cls.author
            )
            for i in range(3)
        ]
        cls.books[0].tags.add(cls.tag)
        cls.user = User.objects.create_user("alice", password="pw")


@both_transports
class _ModelViewSetTests(Fixtures):
    async def test_list_is_paginated(self):
        response = await self.api("get", "/books/")
        assert response.status_code == 200
        assert response.data["count"] == 3
        assert [b["title"] for b in response.data["results"]] == ["Book 0", "Book 1"]
        assert response.data["results"][0]["tags"] == [self.tag.pk]

    async def test_filter_search_ordering(self):
        response = await self.api(
            "get", "/books/", data={"author": self.author.pk, "ordering": "-pages"}
        )
        assert [b["title"] for b in response.data["results"]] == ["Book 2", "Book 1"]
        response = await self.api("get", "/books/", data={"search": "Book 1"})
        assert response.data["count"] == 1
        response = await self.api("get", "/books/", data={"author": 999})
        assert response.status_code == 400
        assert "author" in response.data

    async def test_retrieve_and_404(self):
        response = await self.api("get", f"/books/{self.books[1].pk}/")
        assert response.status_code == 200
        assert response.data["title"] == "Book 1"
        response = await self.api("get", "/books/999/")
        assert response.status_code == 404

    async def test_create(self):
        payload = {
            "title": "New",
            "isbn": "9999999999999",
            "author": self.other.pk,
            "tags": [self.tag.pk],
        }
        response = await self.api("post", "/books/", data=payload)
        assert response.status_code == 201, response.data
        book = await Book.objects.prefetch_related("tags").aget(pk=response.data["id"])
        assert [t.pk async for t in book.tags.all()] == [self.tag.pk]

    async def test_create_validation_errors_match_drf(self):
        payload = {
            "title": "",
            "isbn": self.books[0].isbn,
            "author": 999,
            "tags": [999],
        }
        response = await self.api("post", "/books/", data=payload)
        assert response.status_code == 400
        assert set(response.data) == {"title", "isbn", "author", "tags"}
        assert response.data["isbn"][0].code == "unique"
        assert response.data["author"][0].code == "does_not_exist"

    async def test_update_and_partial_update(self):
        pk = self.books[0].pk
        response = await self.api("patch", f"/books/{pk}/", data={"pages": 7})
        assert response.status_code == 200, response.data
        assert response.data["pages"] == 7
        payload = {
            "title": "Put",
            "isbn": self.books[0].isbn,
            "author": self.other.pk,
            "tags": [],
        }
        response = await self.api("put", f"/books/{pk}/", data=payload)
        assert response.status_code == 200, response.data
        assert response.data["tags"] == []

    async def test_destroy(self):
        response = await self.api("delete", f"/books/{self.books[2].pk}/")
        assert response.status_code == 204
        assert not await Book.objects.filter(pk=self.books[2].pk).aexists()

    async def test_async_and_sync_extra_actions(self):
        response = await self.api(
            "post", f"/books/{self.books[0].pk}/rename/", data={"title": "Renamed"}
        )
        assert response.data == {"title": "Renamed"}
        response = await self.api("get", "/books/count/")
        assert response.data == {"count": 3}

    async def test_options_metadata(self):
        response = await self.api("options", "/books/")
        assert response.status_code == 200
        assert response.data["name"] == "Book List"

    async def test_method_not_allowed(self):
        response = await self.api("delete", "/books/")
        assert response.status_code == 405


@both_transports
class _LegacyViewSetTests(Fixtures):
    """DRF code with sync overrides runs unchanged."""

    async def test_sync_overrides(self):
        self.client.force_authenticate(self.user)
        response = await self.api("get", f"/legacy-books/{self.books[0].pk}/")
        assert response.status_code == 200
        response = await self.api("get", "/legacy-books/", HTTP_X_DENY="1")
        assert response.status_code == 403
        assert response.data["detail"] == "Denied by sync override."

    async def test_sync_perform_create(self):
        self.client.force_authenticate(self.user)
        payload = {
            "title": "Mine",
            "isbn": "1111111111111",
            "author": self.author.pk,
            "tags": [],
        }
        response = await self.api("post", "/legacy-books/", data=payload)
        assert response.status_code == 201, response.data
        book = await Book.objects.aget(pk=response.data["id"])
        assert book.owner_id == self.user.pk


@both_transports
class _NestedTests(Fixtures):
    async def test_nested_with_auto_prefetch(self):
        response = await self.api("get", "/nested-books/")
        assert response.status_code == 200
        first = response.data[0]
        assert first["author"] == {"id": self.author.pk, "name": "Ursula"}
        assert first["tags"] == [{"id": self.tag.pk, "name": "sf"}]
        assert first["author_name"] == "Ursula"

    async def test_retrieve_nested(self):
        response = await self.api("get", f"/nested-books/{self.books[1].pk}/")
        assert response.data["tags"] == []


@both_transports
class _AsyncHookTests(Fixtures):
    @pytest.mark.aiodrf_settings(REPRESENTATION_MODE="thread")
    async def test_async_representation_hooks(self):
        response = await self.api("get", f"/hook-books/{self.books[0].pk}/")
        assert response.status_code == 200
        assert response.data["summary"] == "Book 0 (100 pages)"
        assert response.data["method_summary"] == "Book 0 (100 pages)"
        assert response.data["sync_method"] == "BOOK 0"

    @pytest.mark.aiodrf_settings(REPRESENTATION_MODE="thread")
    async def test_async_validation_hooks(self):
        payload = {
            "title": "book 0",
            "isbn": "2222222222222",
            "author": self.author.pk,
            "tags": [],
        }
        response = await self.api("post", "/hook-books/", data=payload)
        assert response.status_code == 400
        assert response.data == {"title": ["A book with this title already exists."]}

        payload.update(title="  Fresh ", pages=9000)
        response = await self.api("post", "/hook-books/", data=payload)
        assert response.status_code == 400
        assert response.data == {"non_field_errors": ["Too long."]}

        payload["pages"] = 10
        response = await self.api("post", "/hook-books/", data=payload)
        assert response.status_code == 201, response.data
        assert response.data["title"] == "Fresh"
        assert response.data["summary"] == "Fresh (10 pages)"

    async def test_async_hooks_and_db_errors_together(self):
        payload = {
            "title": "book 1",
            "isbn": self.books[0].isbn,
            "author": 999,
            "tags": [],
        }
        response = await self.api("post", "/hook-books/", data=payload)
        assert list(response.data) == ["title", "isbn", "author"]


@both_transports
class _APIViewTests(Fixtures):
    async def test_generic_list_create(self):
        response = await self.api("get", "/authors/")
        assert [a["name"] for a in response.data] == ["Ursula", "Iain"]
        response = await self.api("post", "/authors/", data={"name": "Ann"})
        assert response.status_code == 201

    async def test_async_only_permission(self):
        response = await self.api("get", "/guarded/")
        assert response.status_code == 403
        assert response.data["detail"] == "Missing X-Allow header."
        response = await self.api("get", "/guarded/", HTTP_X_ALLOW="1")
        assert response.status_code == 200

    async def test_mixed_sync_and_async_handlers(self):
        response = await self.api("get", "/mixed/")
        assert response.data == {"handler": "async"}
        response = await self.api("post", "/mixed/")
        assert response.data == {"handler": "sync", "count": 2}

    async def test_function_views(self):
        response = await self.api("get", "/fn/async/")
        assert response.data == {"authors": 2}
        response = await self.api("post", "/fn/async/", data={"a": 1})
        assert response.data == {"echo": {"a": 1}}
        response = await self.api("get", "/fn/sync/")
        assert response.data == {"authors": 2}

    async def test_token_authentication(self):
        token = await Token.objects.acreate(user=self.user)
        response = await self.api("get", "/whoami/")
        assert response.status_code == 401
        response = await self.api(
            "get", "/whoami/", HTTP_AUTHORIZATION=f"Token {token.key}"
        )
        assert response.data == {"username": "alice"}
        response = await self.api("get", "/whoami/", HTTP_AUTHORIZATION="Token nope")
        assert response.status_code == 401

    async def test_force_authenticate(self):
        self.client.force_authenticate(self.user)
        response = await self.api("post", "/whoami/")
        assert response.data == {"username": "alice"}

    async def test_throttling(self):
        self.client.force_authenticate(self.user)
        for _ in range(3):
            assert (await self.api("get", "/throttled/")).status_code == 200
        response = await self.api("get", "/throttled/")
        assert response.status_code == 429


@both_transports
class _SchemaTests(Fixtures):
    async def test_schema_generation(self):
        response = await self.api("get", "/schema/", data={"format": "json"})
        assert response.status_code == 200
        import json

        schema = json.loads(response.content)
        books = schema["paths"]["/books/"]
        list_params = {p["name"] for p in books["get"]["parameters"]}
        assert {"page", "author", "search", "ordering"} <= list_params
        assert books["get"]["responses"]["200"]["content"]["application/json"][
            "schema"
        ]["$ref"].endswith("PaginatedBookList")
        assert "201" in books["post"]["responses"]


class HopBudgetTests(Fixtures, TestCase):
    async def test_hop_budgets(self):
        from aiodrf.test import AsyncAPIClient

        client = AsyncAPIClient()
        token = await Token.objects.acreate(user=self.user)
        with count_hops() as hops:
            response = await client.get(f"/books/{self.books[0].pk}/")
        assert response.status_code == 200
        # One hop: lookup, the unloaded M2M ``tags`` and the representation.
        assert hops.calls == ["RetrieveModelMixin._retrieve"]

        with count_hops() as hops:
            response = await client.get(
                "/whoami/", HTTP_AUTHORIZATION=f"Token {token.key}"
            )
        # Session lookup through Django's ``auser`` is not an aiodrf hop;
        # token authentication is one.
        assert hops.count == 1, hops.calls

        with count_hops() as hops:
            response = await client.get("/nested-books/")
        # One hop for the prefetched list; representation stays inline.
        assert hops.count == 1, hops.calls
