"""List serializers whose child refers to them weakly (``aiodrf.contrib.list_serializers``)."""

import gc
import weakref

import pytest
from rest_framework import serializers as drf

from aiodrf import aio, serializers
from aiodrf.contrib.list_serializers import ListSerializer, SchemaListSerializer
from tests.testapp.models import Author

msgspec = pytest.importorskip("msgspec")


class AuthorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Author
        fields = ["id", "name"]
        list_serializer_class = ListSerializer


class Base(serializers.ModelSerializer):
    # A project's base: every ``many=True`` of its subclasses.
    default_list_serializer_class = ListSerializer


class BaseAuthorSerializer(Base):
    class Meta:
        model = Author
        fields = ["id", "name"]


class ContextField(drf.Field):
    def to_representation(self, value):
        return self.context["marker"]


class WithContext(serializers.Serializer):
    name = drf.CharField()
    marker = ContextField(source="*")

    class Meta:
        list_serializer_class = ListSerializer


def _authors():
    return [Author(id=index, name=f"author {index}") for index in range(3)]


@pytest.mark.parametrize("serializer_class", [AuthorSerializer, BaseAuthorSerializer])
async def test_the_list_is_the_same_list_for_its_child(serializer_class):
    serializer = serializer_class(_authors(), many=True)
    assert type(serializer) is ListSerializer
    assert serializer.child.parent == serializer
    assert serializer.child.root == serializer
    assert await aio.data(serializer) == [
        {"id": index, "name": f"author {index}"} for index in range(3)
    ]


@pytest.mark.aiodrf_settings(SERIALIZER_BACKEND_FALLBACK="drf")
async def test_the_child_reads_the_lists_context():
    serializer = WithContext(
        [{"name": "a"}, {"name": "b"}], many=True, context={"marker": "seen"}
    )
    assert await aio.data(serializer) == [
        {"name": "a", "marker": "seen"},
        {"name": "b", "marker": "seen"},
    ]


def test_the_list_goes_without_the_cyclic_collector():
    # DRF's child binding made the list and its child a cycle.
    serializer = AuthorSerializer(_authors(), many=True)
    assert len(serializer.data) == 3
    reference = weakref.ref(serializer)
    gc.collect()
    gc.disable()
    try:
        del serializer
        assert reference() is None
    finally:
        gc.enable()


def test_instances_go_with_a_schema_list_without_the_cyclic_collector():
    # A schema serializer builds no bound fields: nothing else holds the child.
    class Row(msgspec.Struct, weakref=True):
        name: str

    from aiodrf.contrib.msgspec import MsgspecSerializer

    class RowSerializer(MsgspecSerializer):
        default_list_serializer_class = SchemaListSerializer

        class Meta:
            schema = Row

    rows = [Row(name="a"), Row(name="b")]
    references = [weakref.ref(row) for row in rows]
    serializer = RowSerializer(rows, many=True)
    assert serializer.data == [{"name": "a"}, {"name": "b"}]
    del rows
    gc.collect()
    gc.disable()
    try:
        del serializer
        assert all(reference() is None for reference in references)
    finally:
        gc.enable()


def test_a_schema_serializer_has_a_weak_list_too():
    class Book(msgspec.Struct):
        title: str

    from aiodrf.contrib.msgspec import MsgspecSerializer

    class BookSerializer(MsgspecSerializer):
        default_list_serializer_class = SchemaListSerializer

        class Meta:
            schema = Book

    serializer = BookSerializer([Book(title="a")], many=True)
    assert type(serializer) is SchemaListSerializer
    assert serializer.child.parent == serializer
    assert serializer.data == [{"title": "a"}]
