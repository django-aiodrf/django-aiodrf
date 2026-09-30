"""Explicit factory and Django-style list serializer selection."""

from typing import Any, assert_type

from aiodrf import serializers
from aiodrf.contrib.builtin.concurrent import ConcurrentListSerializer


class EnrichedItems(ConcurrentListSerializer):
    max_concurrency = 3

    def get_item_serializer(self, instance: Any) -> "Item":
        return Item(instance, context=dict(self.context))


class Item(serializers.Serializer):
    value = serializers.IntegerField()

    class Meta:
        list_serializer_class = EnrichedItems


async def represent(serializer: EnrichedItems) -> None:
    assert_type(await serializer.ato_representation([{"value": 1}]), list[Any])
