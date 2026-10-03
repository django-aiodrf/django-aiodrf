"""Avoid identity-only thread hops without moving custom field I/O onto the loop."""

import threading

import pytest
from django.test import override_settings
from rest_framework import serializers

from aiodrf import aio
from aiodrf.test import count_hops


@pytest.mark.parametrize("rows", [1, 100])
@pytest.mark.parametrize("mode", ["thread", "inline"])
async def test_async_method_field_identity_lookup_does_not_hop_per_row(rows, mode):
    seen = []

    class Item(serializers.Serializer):
        value = serializers.SerializerMethodField()

        async def get_value(self, instance):
            seen.append(instance)
            return instance["value"]

    data = [{"value": n} for n in range(rows)]
    with (
        override_settings(AIODRF={"REPRESENTATION_MODE": mode}, FASTDRF={}),
        count_hops() as hops,
    ):
        result = await aio.data(Item(data, many=True))
    assert result == data
    assert all(actual is expected for actual, expected in zip(seen, data, strict=True))
    # Thread mode still classifies the serializer in the worker. No row-wise hops.
    assert hops.count == (1 if mode == "thread" else 0), hops.calls


@pytest.mark.aiodrf_settings(REPRESENTATION_MODE="thread")
@pytest.mark.parametrize("override", ["subclass", "instance", "source"])
async def test_custom_method_field_attribute_lookup_stays_in_worker(override):
    threads = []

    class Custom(serializers.SerializerMethodField):
        def get_attribute(self, instance):
            threads.append(threading.get_ident())
            return super().get_attribute(instance)

    class Record:
        @property
        def child(self):
            threads.append(threading.get_ident())
            return self

    class Item(serializers.Serializer):
        value = (
            Custom() if override == "subclass" else serializers.SerializerMethodField()
        )

        async def get_value(self, instance):
            return 1

    serializer = Item(Record())
    field = serializer.fields["value"]
    if override == "instance":
        original = field.get_attribute

        def lookup(instance):
            threads.append(threading.get_ident())
            return original(instance)

        field.get_attribute = lookup
    elif override == "source":
        field.source_attrs = ["child"]
    with count_hops() as hops:
        assert await aio.data(serializer) == {"value": 1}
    assert len(threads) == 1
    assert threads[0] != threading.get_ident()
    assert hops.count == 2


async def test_method_field_order_and_exception_short_circuit_are_unchanged():
    calls = []

    class Item(serializers.Serializer):
        first = serializers.SerializerMethodField()
        second = serializers.SerializerMethodField()

        async def get_first(self, instance):
            calls.append((instance, "first"))
            return instance

        async def get_second(self, instance):
            calls.append((instance, "second"))
            if instance == 2:
                raise ValueError("stop")
            return instance

    with pytest.raises(ValueError, match="stop"):
        await aio.data(Item([1, 2, 3], many=True))
    assert calls == [(1, "first"), (1, "second"), (2, "first"), (2, "second")]
