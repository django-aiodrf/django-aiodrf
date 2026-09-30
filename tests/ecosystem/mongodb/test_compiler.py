"""
``SERIALIZER_BACKEND`` on MongoDB models. With ``aiodrf.contrib.mongodb``
installed, the output compiler represents ``ObjectId`` values as
django-mongodb-extensions' ``ObjectIdField`` and aiodrf's
``ObjectIdPrimaryKeyRelatedField`` do; embedded models and arrays stay on
DRF, with a reason. The input recognizer declines ``ObjectId`` fields.
"""

import pytest
from django.test import TestCase, override_settings
from django.urls import path
from django_mongodb_extensions.rest_framework import MongoModelSerializer, ObjectIdField
from rest_framework import serializers
from rest_framework import viewsets as drf_viewsets

from aiodrf import viewsets
from aiodrf.contrib import compiler, inputs
from aiodrf.contrib.mongodb.fields import ObjectIdPrimaryKeyRelatedField
from aiodrf.test import AsyncAPIClient
from tests.ecosystem.mongodb.models import Author, Book, Tag


def serializer(base, fields, **attrs):
    meta = type("Meta", (), {"model": Book, "fields": fields})
    return type("BookSerializer", (base,), {"Meta": meta, **attrs})()


@pytest.mark.parametrize(
    ("base", "fields", "attrs", "output", "input_error"),
    [
        (
            serializers.ModelSerializer,
            ["id"],
            {},
            "id is a IntegerField on a ObjectIdAutoField",
            None,
        ),
        (MongoModelSerializer, ["id"], {}, None, None),
        (
            serializers.ModelSerializer,
            ["author"],
            {},
            "author points to a ObjectIdAutoField key",
            "author is a PrimaryKeyRelatedField",
        ),
        (
            MongoModelSerializer,
            ["author", "tags"],
            {"serializer_related_field": ObjectIdPrimaryKeyRelatedField},
            None,
            "author is a ObjectIdPrimaryKeyRelatedField",
        ),
        (
            MongoModelSerializer,
            ["author"],
            {
                "Meta": type(
                    "Meta",
                    (),
                    {
                        "model": Book,
                        "fields": ["author"],
                        "extra_kwargs": {"author": {"pk_field": ObjectIdField()}},
                    },
                )
            },
            None,
            "author is a PrimaryKeyRelatedField",
        ),
        (
            MongoModelSerializer,
            ["address"],
            {},
            "address is not a forward foreign key",
            "AddressSerializer overrides a validation method",
        ),
        (
            MongoModelSerializer,
            ["keywords"],
            {},
            "keywords is a ListField",
            # Input: canonical lists of strings are recognized as DRF validates them.
            None,
        ),
        (MongoModelSerializer, ["ref"], {}, None, "ref is a ObjectIdField"),
        (MongoModelSerializer, ["title", "pages"], {}, None, None),
    ],
)
def test_what_compiles(base, fields, attrs, output, input_error):
    instance = serializer(base, fields, **attrs)
    assert compiler.report(instance, backend="msgspec") == (
        output and f"BookSerializer.{output}"
    )
    if input_error is None:
        inputs.analyze_input(instance, backend="msgspec")
    else:
        with pytest.raises(compiler.NotCompilable, match=input_error):
            inputs.analyze_input(instance, backend="msgspec")


class BookSerializer(MongoModelSerializer):
    serializer_related_field = ObjectIdPrimaryKeyRelatedField

    class Meta:
        model = Book
        fields = ["id", "title", "pages", "author", "tags", "ref"]


class TitleSerializer(MongoModelSerializer):
    class Meta:
        model = Book
        fields = ["title", "pages"]


def viewset(base, serializer_class):
    return type(
        "Books",
        (base,),
        {
            "queryset": Book.objects.order_by("title"),
            "serializer_class": serializer_class,
        },
    )


urlpatterns = [
    path(f"{prefix}/{name}/", viewset(base, serializer_class).as_view({"get": "list"}))
    for prefix, base in (
        ("drf", drf_viewsets.ModelViewSet),
        ("aiodrf", viewsets.ModelViewSet),
    )
    for name, serializer_class in (
        ("books", BookSerializer),
        ("titles", TitleSerializer),
    )
]


@override_settings(ROOT_URLCONF=__name__, AIODRF={"SERIALIZER_BACKEND": "msgspec"})
class CompiledViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        author = Author.objects.create(name="Ann")
        tag = Tag.objects.create(name="t")
        for title in "ab":
            book = Book.objects.create(title=title, author=author, ref=author.pk)
            book.tags.add(tag)

    def test_both_serializers_are_compiled(self):
        books = Book.objects.all()
        assert compiler.compiled_for(TitleSerializer(books, many=True)) is not None
        assert compiler.compiled_for(BookSerializer(books, many=True)) is not None

    async def test_both_answer_as_drf(self):
        client = AsyncAPIClient()
        for name in ("books", "titles"):
            drf = await client.get(f"/drf/{name}/")
            response = await client.get(f"/aiodrf/{name}/")
            assert response.status_code == drf.status_code == 200
            assert response.content == drf.content
