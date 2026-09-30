"""
aiodrf's ``Serializer`` represents model instances as DRF's does: loaded,
deferred and callable values, relations, properties, other sources, and
fields changed between the objects of one representation.
"""

import datetime
import decimal
import uuid

import pytest
from django.test.utils import isolate_apps
from rest_framework import serializers as drf

from aiodrf import serializers
from tests.testapp.models import Author, Book, Edition


def both(aiodrf_class, instance, **kwargs):
    """aiodrf's representation and DRF's, of a class with the same fields."""
    drf_class = type(
        "DRF" + aiodrf_class.__name__,
        (drf.ModelSerializer,),
        {
            **{
                name: field
                for name, field in vars(aiodrf_class).items()
                if isinstance(field, drf.Field)
            },
            "Meta": aiodrf_class.Meta,
        },
    )
    return aiodrf_class(instance, **kwargs).data, drf_class(instance, **kwargs).data


class Editions(serializers.ModelSerializer):
    class Meta:
        model = Edition
        fields = [
            "id",
            "code",
            "published",
            "released",
            "active",
            "rating",
            "price",
            "format",
            "extra",
            "notes",
            "book",
            "translator",
        ]


def edition(**values):
    return Edition(
        pk=3,
        code=uuid.UUID(int=7),
        book_id=1,
        published=datetime.datetime(2026, 9, 29, 12, 30, tzinfo=datetime.UTC),
        released=datetime.date(2026, 9, 29),
        rating=None,
        price=decimal.Decimal("1.50"),
        format="pb",
        extra={"a": [1]},
        **values,
    )


def test_loaded_columns_are_drfs_output():
    ours, theirs = both(Editions, edition())
    assert ours == theirs
    ours, theirs = both(Editions, [edition(), edition(notes="n")], many=True)
    assert ours == theirs


def test_a_callable_value_is_called_as_drf_calls_it():
    instance = edition(notes="text")
    instance.__dict__["notes"] = lambda: "called"
    ours, theirs = both(Editions, instance)
    assert ours == theirs
    assert ours["notes"] == "called"


@pytest.mark.django_db
def test_a_deferred_column_is_read_as_drf_reads_it():
    author = Author.objects.create(name="Ada")
    Book.objects.create(title="T", isbn="1", author=author)

    class Books(serializers.ModelSerializer):
        class Meta:
            model = Book
            fields = ["id", "title", "pages"]

    book = Book.objects.defer("title").get()
    ours, theirs = both(Books, book)
    assert ours == theirs
    assert ours["title"] == "T"


class Sources(serializers.ModelSerializer):
    whole = drf.CharField(source="*", read_only=True)
    nickname = drf.CharField(source="name", read_only=True)
    missing = drf.CharField(read_only=True, default="none")
    method = drf.SerializerMethodField()

    class Meta:
        model = Author
        fields = ["id", "name", "whole", "nickname", "missing", "method"]

    def get_method(self, author):
        return author.name.upper()


def test_other_sources_are_drfs():
    author = Author(pk=1, name="Ada")
    ours = Sources(author).data
    assert ours["nickname"] == "Ada"
    assert ours["missing"] == "none"
    assert ours["method"] == "ADA"
    assert ours["whole"] == str(author)


def test_a_mapping_is_represented_by_drf():
    relations = {"book": None, "translator": None}
    values = {
        name: getattr(edition(), name)
        for name in Editions.Meta.fields
        if name not in relations
    } | relations
    ours, theirs = both(Editions, values)
    assert ours == theirs


def test_a_field_changed_on_the_instance_keeps_its_own_code():
    serializer = Editions(edition(notes="n"))
    serializer.fields["notes"].to_representation = lambda value: value.upper()
    assert serializer.data["notes"] == "N"


@isolate_apps("tests.testapp")
def test_a_property_over_a_column_is_read_as_drf_reads_it():
    from django.db import models

    class Person(models.Model):
        name = models.CharField(max_length=10)

        class Meta:
            app_label = "testapp"

        def __str__(self):
            return self.name

    class Shouting(Person):
        class Meta:
            proxy = True
            app_label = "testapp"

        def __str__(self):
            return self.name

        @property
        def name(self):
            return self.__dict__["name"].upper()

        @name.setter
        def name(self, value):
            self.__dict__["name"] = value

    class People(serializers.ModelSerializer):
        class Meta:
            model = Shouting
            fields = ["id", "name"]

    assert People(Shouting(pk=1, name="ada")).data["name"] == "ADA"


def _changed_after_two_items(change):
    class Names(serializers.ModelSerializer):
        class Meta:
            model = Author
            fields = ["id", "name"]

    serializer = Names()
    first, second = Author(pk=1, name="a"), Author(pk=2, name="b")
    serializer.to_representation(first)
    serializer.to_representation(second)
    change(serializer)
    ours = serializer.to_representation(first)
    return ours, drf.Serializer.to_representation(serializer, first)


@pytest.mark.parametrize(
    "change",
    [
        pytest.param(
            lambda s: s.fields.__setitem__("name", drf.CharField(source="pk")),
            id="field replaced",
        ),
        pytest.param(
            lambda s: setattr(s.fields["name"], "get_attribute", lambda i: "own"),
            id="get_attribute assigned",
        ),
        pytest.param(
            lambda s: setattr(s.fields["name"], "write_only", True),
            id="made write-only",
        ),
    ],
)
def test_fields_changed_between_items_are_drfs(change):
    ours, theirs = _changed_after_two_items(change)
    assert ours == theirs


class Hiding(serializers.ModelSerializer):
    class Meta:
        model = Author
        fields = ["id", "name"]

    @property
    def _readable_fields(self):
        hidden = self.context.get("hide")
        for field in self.fields.values():
            if not field.write_only and not (hidden and field.field_name == "name"):
                yield field


def test_readable_fields_of_the_projects_are_read_for_each_item():
    serializer = Hiding(context={})
    output = []
    for index in range(4):
        serializer.context["hide"] = index >= 2
        output.append(serializer.to_representation(Author(pk=index, name="n")))
    assert [sorted(item) for item in output] == [
        ["id", "name"],
        ["id", "name"],
        ["id"],
        ["id"],
    ]
