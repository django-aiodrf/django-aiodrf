"""Lazy DRF field hooks must not execute on the event loop in thread mode."""

import threading

import pytest
from django.test import override_settings
from django.utils.asyncio import async_unsafe

from aiodrf import aio, serializers
from tests.testapp.models import Author


@pytest.mark.aiodrf_settings(REPRESENTATION_MODE="thread")
@pytest.mark.parametrize("entry", ["data", "representation", "default"])
@pytest.mark.parametrize("shape", ["root", "nested", "many", "late_nested"])
async def test_override_calling_super_prepares_fields_in_worker(entry, shape):
    built = []

    class Item(serializers.Serializer):
        value = serializers.IntegerField()

        @async_unsafe("field construction on loop")
        def get_fields(self):
            built.append(threading.get_ident())
            return super().get_fields()

        async def ato_representation(self, obj):
            return await super().ato_representation(obj)

    class Parent(serializers.Serializer):
        # Classification of this first field must not leave later fields unsafe.
        first = serializers.SerializerMethodField()
        child = Item(many=shape == "many")

        async def get_first(self, obj):
            return "first"

    instance = {"value": 1}
    candidate = Item(instance)
    expected = instance
    if shape != "root":
        instance = {"child": [instance] if shape == "many" else instance}
        candidate = Parent(instance)
        if shape != "late_nested":
            candidate.fields.pop("first")
        expected = {
            **({"first": "first"} if shape == "late_nested" else {}),
            **instance,
        }
    if entry == "data":
        result = await aio.data(candidate)
    elif entry == "representation":
        result = await aio.to_representation(candidate, instance)
    else:
        result = await aio.default_to_representation(candidate, instance)
    assert result == expected
    assert len(built) == 1
    assert built[0] != threading.get_ident()


@pytest.mark.aiodrf_settings(SERIALIZER_BACKEND_FALLBACK="drf")
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_fieldless_base_serializer_has_no_declared_fields_requirement(
    asynchronous,
):
    class Item(serializers.BaseSerializer):
        if asynchronous:

            async def ato_representation(self, obj):
                return {"value": obj}

        else:

            def to_representation(self, obj):
                return {"value": obj}

    assert await aio.data(Item(1)) == {"value": 1}


async def test_explicit_inline_mode_preserves_field_construction_policy():
    built = []

    class Item(serializers.Serializer):
        value = serializers.IntegerField()

        def get_fields(self):
            built.append(threading.get_ident())
            return super().get_fields()

        async def ato_representation(self, obj):
            return await super().ato_representation(obj)

    with override_settings(AIODRF={"REPRESENTATION_MODE": "inline"}):
        assert await Item({"value": 1}).adata() == {"value": 1}
    assert built == [threading.get_ident()]


@pytest.mark.parametrize("base", [object, list, tuple])
async def test_custom_sync_source_is_materialized_in_worker(base):
    class Source(base):
        @async_unsafe("source iteration on loop")
        def __iter__(self):
            return iter([1, 2])

    class Item(serializers.Serializer):
        value = serializers.SerializerMethodField()

        async def get_value(self, obj):
            return obj

    assert await Item(Source(), many=True).adata() == [{"value": 1}, {"value": 2}]


@pytest.mark.aiodrf_settings(REPRESENTATION_MODE="thread")
@pytest.mark.django_db(transaction=True)
async def test_async_super_preserves_querying_dynamic_fields(worker_connections):
    await Author.objects.acreate(name="visible")

    class Item(serializers.Serializer):
        value = serializers.IntegerField()

        def get_fields(self):
            fields = super().get_fields()
            if not Author.objects.filter(name=self.context["tenant"]).exists():
                fields.pop("value")
            return fields

        async def ato_representation(self, obj):
            return await super().ato_representation(obj)

    for tenant, expected in (("visible", {"value": 1}), ("absent", {})):
        assert await Item({"value": 1}, context={"tenant": tenant}).adata() == expected
