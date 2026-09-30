"""
The same requests to DRF's viewsets and to aiodrf's, compared.

Each request runs against DRF's ``ModelViewSet`` and against aiodrf's, with
aiodrf's serializers and with DRF's, and everything observable is compared:
status, headers, body (or the exception a view raises), and the database
afterwards. The reference is DRF itself, so these need no update when DRF
changes behaviour. Each profile is a set of ``AIODRF`` options; the options
that depart from DRF's behaviour are not in them. The compiled profiles
assert that compiled encoders produced output, so that the session cannot
stop covering them unnoticed.

Run by ``nox -s differential`` (``tests/differential`` is not collected by
the default session).
"""

import datetime
import json
import uuid

import pytest
from asgiref.sync import sync_to_async
from django.db import transaction
from django.test import override_settings
from django.urls import include, path
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, pagination, routers
from rest_framework import serializers as drf_serializers
from rest_framework import viewsets as drf_viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny

from aiodrf import serializers as aiodrf_serializers
from aiodrf import viewsets as aiodrf_viewsets
from aiodrf.contrib import compiler
from aiodrf.test import APIClient, AsyncAPIClient
from tests.testapp.models import Author, Book, Edition, Tag

PROFILES = {
    "default": {},
    "msgspec": {
        "SERIALIZER_BACKEND": "msgspec",
        "CACHE_SERIALIZER_FIELDS": True,
        "FIELD_COPY_MODE": "compiled",
        "REPRESENTATION_MODE": "inline",
        "BATCH_RELATED_LOOKUPS": True,
    },
    "pydantic": {
        "SERIALIZER_BACKEND": "pydantic",
        "CACHE_SERIALIZER_FIELDS": True,
        "FIELD_COPY_MODE": "clone",
    },
    # Output compiled without msgspec or pydantic; input stays DRF's.
    "python": {
        "SERIALIZER_BACKEND": "python",
        "CACHE_SERIALIZER_FIELDS": True,
    },
}


def serializers_for(module):
    class AuthorSerializer(module.ModelSerializer):
        class Meta:
            model = Author
            fields = ["id", "name"]

    class TagSerializer(module.ModelSerializer):
        class Meta:
            model = Tag
            fields = ["id", "name"]

    class BookSerializer(module.ModelSerializer):
        author_detail = AuthorSerializer(source="author", read_only=True)
        title = module.CharField(max_length=20)

        class Meta:
            model = Book
            fields = ["id", "title", "isbn", "pages", "author", "author_detail", "tags"]

        def validate_pages(self, value):
            if value == 13:
                raise module.ValidationError("Unlucky.")
            return value

    # The three below compile with strict parity.
    class PlainBookSerializer(module.ModelSerializer):
        class Meta:
            model = Book
            fields = ["id", "title", "isbn", "pages", "author"]

    class NestedBookSerializer(module.ModelSerializer):
        author = AuthorSerializer(read_only=True)
        tags = TagSerializer(many=True, read_only=True)

        class Meta:
            model = Book
            fields = ["id", "title", "pages", "author", "tags"]

    class EditionOutSerializer(module.ModelSerializer):
        class Meta:
            model = Edition
            fields = [
                "id",
                "code",
                "book",
                "translator",
                "released",
                "active",
                "rating",
                "format",
                "notes",
            ]

    # Every field type, for input: UUID, datetime, date, decimal, JSON, choices.
    class EditionSerializer(module.ModelSerializer):
        class Meta:
            model = Edition
            fields = "__all__"

    return {
        "books": BookSerializer,
        "plain-books": PlainBookSerializer,
        "nested-books": NestedBookSerializer,
        "editions": EditionSerializer,
        "edition-outputs": EditionOutSerializer,
    }


DRF_SERIALIZERS = serializers_for(drf_serializers)
AIODRF_SERIALIZERS = serializers_for(aiodrf_serializers)
COMPILED = ("plain-books", "nested-books", "edition-outputs")


class Pages(pagination.PageNumberPagination):
    page_size = 2


class Limits(pagination.LimitOffsetPagination):
    default_limit = 2


class Cursor(pagination.CursorPagination):
    page_size = 2
    ordering = "id"


PAGINATORS = {"none": None, "pages": Pages, "limits": Limits, "cursor": Cursor}


def boom(self, request):
    raise RuntimeError("a view's own error")


def queryset(resource):
    if "edition" in resource:
        return Edition.objects.order_by("id")
    # Loaded as inline representation requires; the same for DRF.
    return Book.objects.select_related("author").prefetch_related("tags")


READ_ONLY = {
    drf_viewsets.ModelViewSet: drf_viewsets.ReadOnlyModelViewSet,
    aiodrf_viewsets.ModelViewSet: aiodrf_viewsets.ReadOnlyModelViewSet,
}


def viewset(base, resource, serializer_class, paginator):
    attributes = {
        "queryset": queryset(resource),
        "serializer_class": serializer_class,
        "pagination_class": paginator,
        "permission_classes": [AllowAny],
        "authentication_classes": [],
    }
    if resource == "books":
        attributes |= {
            "filter_backends": [
                DjangoFilterBackend,
                filters.SearchFilter,
                filters.OrderingFilter,
            ],
            "filterset_fields": ["author", "pages"],
            "search_fields": ["title"],
            "ordering_fields": ["pages", "id"],
            "boom": action(detail=False)(boom),
        }
    if resource == "edition-outputs":
        base = READ_ONLY[base]
    return type("Resource", (base,), attributes)


IMPLEMENTATIONS = {
    "drf": (drf_viewsets.ModelViewSet, DRF_SERIALIZERS),
    "aiodrf": (aiodrf_viewsets.ModelViewSet, AIODRF_SERIALIZERS),
    "aiodrf-drf-serializer": (aiodrf_viewsets.ModelViewSet, DRF_SERIALIZERS),
}

urlpatterns = []
for paginator_name, paginator in PAGINATORS.items():
    for name, (base, serializer_classes) in IMPLEMENTATIONS.items():
        router = routers.SimpleRouter()
        for resource, serializer_class in serializer_classes.items():
            router.register(
                resource, viewset(base, resource, serializer_class, paginator), resource
            )
        urlpatterns.append(path(f"{name}/{paginator_name}/", include(router.urls)))


def requests(author, other, tag, book, edition):
    body = {"title": "New", "isbn": "N1", "pages": 5, "author": author, "tags": [tag]}
    edition_body = {
        "code": str(uuid.UUID(int=9)),
        "book": book,
        "translator": other,
        "published": "2026-09-30T10:00:00Z",
        "released": "2026-09-30",
        "active": False,
        "rating": 4.5,
        "price": "12.50",
        "format": "hb",
        "extra": {"a": [1, None]},
        "notes": "n",
    }
    return [
        # Reads, pages, filters.
        ("get", "books/", None),
        ("get", "books/?page=2", None),
        ("get", "books/?page=9", None),
        ("get", "books/?page=x", None),
        ("get", "books/?limit=1&offset=1", None),
        ("get", "books/?limit=x", None),
        ("get", f"books/?author={author}", None),
        ("get", "books/?author=abc", None),
        ("get", "books/?pages=11", None),
        ("get", "books/?search=B1", None),
        ("get", "books/?ordering=-pages", None),
        ("get", "books/?ordering=nope", None),
        ("head", "books/", None),
        ("options", "books/", None),
        ("options", f"books/{book}/", None),
        ("get", f"books/{book}/", None),
        ("get", "books/999/", None),
        ("get", "books/x/", None),
        ("get", "books/boom/", None),
        ("get", "plain-books/", None),
        ("get", f"plain-books/{book}/", None),
        ("get", "nested-books/", None),
        ("get", f"nested-books/{book}/", None),
        ("get", "editions/", None),
        ("get", f"editions/{edition}/", None),
        ("get", "edition-outputs/", None),
        ("get", f"edition-outputs/{edition}/", None),
        ("post", "edition-outputs/", {}),  # 405
        # Book writes.
        ("post", "books/", body),
        ("post", "books/", {**body, "isbn": "I0"}),  # unique
        ("post", "books/", {**body, "title": "x" * 21}),
        ("post", "books/", {**body, "title": ""}),
        ("post", "books/", {**body, "title": None}),
        ("post", "books/", {**body, "pages": 13}),
        ("post", "books/", {**body, "pages": -1}),
        ("post", "books/", {**body, "pages": "many"}),
        ("post", "books/", {**body, "pages": 1.5}),
        ("post", "books/", {**body, "pages": True}),
        ("post", "books/", {**body, "author": 999}),
        ("post", "books/", {**body, "author": None}),
        ("post", "books/", {**body, "author": "abc"}),
        ("post", "books/", {**body, "tags": [999]}),
        ("post", "books/", {**body, "tags": [tag, tag]}),
        ("post", "books/", {**body, "tags": "not a list"}),
        ("post", "books/", {key: v for key, v in body.items() if key != "isbn"}),
        ("post", "books/", {**body, "unknown": 1}),
        ("post", "books/", {}),
        ("post", "books/", [body]),
        ("post", "books/", "text"),
        (
            "post",
            "plain-books/",
            {key: body[key] for key in ("title", "isbn", "author")},
        ),
        ("post", "nested-books/", {"title": "N", "pages": 3}),
        ("put", f"books/{book}/", {**body, "isbn": "P1", "author": other}),
        ("put", f"books/{book}/", {"title": "Only"}),
        ("patch", f"books/{book}/", {"pages": 7}),
        ("patch", f"books/{book}/", {"pages": 13}),
        ("patch", f"books/{book}/", {"tags": []}),
        ("patch", f"books/{book}/", {"isbn": "I0"}),
        ("patch", "books/999/", {"pages": 7}),
        ("delete", f"books/{book}/", None),
        ("delete", "books/999/", None),
        ("put", "books/", body),  # 405
        # Edition writes: every field type.
        ("post", "editions/", edition_body),
        ("post", "editions/", {**edition_body, "code": "not a uuid"}),
        ("post", "editions/", {**edition_body, "published": "yesterday"}),
        ("post", "editions/", {**edition_body, "published": "2026-09-30T10:00:00"}),
        ("post", "editions/", {**edition_body, "released": "2026-13-01"}),
        ("post", "editions/", {**edition_body, "released": None}),
        ("post", "editions/", {**edition_body, "rating": "nan"}),
        ("post", "editions/", {**edition_body, "rating": None}),
        ("post", "editions/", {**edition_body, "price": "1.234"}),
        ("post", "editions/", {**edition_body, "price": "12345.00"}),
        ("post", "editions/", {**edition_body, "price": 3}),
        ("post", "editions/", {**edition_body, "format": "ebook"}),
        ("post", "editions/", {**edition_body, "translator": None}),
        ("post", "editions/", {**edition_body, "active": "yes"}),
        ("post", "editions/", {**edition_body, "extra": "not json object"}),
        ("patch", f"editions/{edition}/", {"notes": "changed", "rating": 1}),
        ("put", f"editions/{edition}/", edition_body),
        ("delete", f"editions/{edition}/", None),
    ]


READS = {"get", "head", "options"}


def snapshot():
    return {
        "books": list(Book.objects.order_by("id").values()),
        "authors": list(Author.objects.order_by("id").values()),
        "tags": list(Book.tags.through.objects.order_by("id").values()),
        "editions": list(Edition.objects.order_by("id").values()),
    }


def described(response, prefix):
    # Pagination links name the implementation's own URL.
    content = response.content.replace(prefix.encode(), b"/")
    headers = dict(response.items())
    if "Content-Length" in headers:
        headers["Content-Length"] = str(len(content))
    if "Location" in headers:
        headers["Location"] = headers["Location"].replace(prefix, "/")
    return {
        "status": response.status_code,
        "headers": headers,
        "body": json.loads(content) if content else None,
    }


def observe(client, method, prefix, url, data):
    with transaction.atomic():
        try:
            # A savepoint: a database error leaves the snapshot possible.
            with transaction.atomic():
                response = getattr(client, method)(prefix + url, data, format="json")
        except Exception as exc:  # noqa: BLE001 -- the view's own error, compared
            result = {"raised": (type(exc), str(exc))}
        else:
            result = described(response, prefix)
        result["database"] = snapshot()
        transaction.set_rollback(True)
    return result


def fixtures():
    authors = [Author.objects.create(name=name) for name in ("Ann", "Bo")]
    tags = [Tag.objects.create(name=name) for name in ("a", "b")]
    for index in range(3):
        book = Book.objects.create(
            title=f"B{index}", isbn=f"I{index}", pages=10 + index, author=authors[0]
        )
        book.tags.set(tags[: index % 3])
    edition = None
    for index, (released, rating, translator) in enumerate(
        [(datetime.date(2026, 1, 2), 3.5, authors[1]), (None, None, None)]
    ):
        edition = Edition.objects.create(
            code=uuid.UUID(int=index + 1),
            book=book,
            translator=translator,
            published=datetime.datetime(2026, 9, 30, 8, tzinfo=datetime.UTC),
            released=released,
            rating=rating,
            price="1.50",
            format="pb",
            extra={"k": index},
        )
    return requests(authors[0].pk, authors[1].pk, tags[0].pk, book.pk, edition.pk)


def compiled_encoders():
    """The resources of ``COMPILED`` whose aiodrf serializer has an encoder."""
    return {
        resource
        for resource in COMPILED
        if any(
            isinstance(value, compiler.Encoder)
            for value in (
                compiler._compiled_by_class.get(AIODRF_SERIALIZERS[resource]) or {}
            ).values()
        )
    }


@pytest.mark.django_db
@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("paginator", PAGINATORS)
@override_settings(ROOT_URLCONF=__name__)
def test_aiodrf_answers_as_drf(profile, paginator):
    compiler._compiled_by_class.clear()
    client = APIClient()
    compared = 0
    with override_settings(AIODRF=PROFILES[profile]):
        cases = fixtures()
        for method, url, data in cases:
            reference = observe(client, method, f"/drf/{paginator}/", url, data)
            for name in ("aiodrf", "aiodrf-drf-serializer"):
                ours = observe(client, method, f"/{name}/{paginator}/", url, data)
                assert ours == reference, (name, method, url, data)
                compared += 1
        if profile != "default":
            assert compiled_encoders() == set(COMPILED)
    assert compared == 2 * len(cases)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("profile", PROFILES)
@override_settings(ROOT_URLCONF=__name__)
async def test_aiodrf_answers_as_drf_through_asgi(profile):
    # The deployed path: Django's async handler. Requests that write are
    # compared through the other test, which rolls each one back.
    client = AsyncAPIClient()
    with override_settings(AIODRF=PROFILES[profile]):
        cases = await sync_to_async(fixtures)()
        for method, url, data in cases:
            if method not in READS or url.endswith("boom/"):
                continue
            for paginator in PAGINATORS:
                results = {}
                for name in IMPLEMENTATIONS:
                    prefix = f"/{name}/{paginator}/"
                    response = await getattr(client, method)(prefix + url, data)
                    results[name] = described(response, prefix)
                reference = results.pop("drf")
                for name, ours in results.items():
                    assert ours == reference, (name, paginator, method, url)
