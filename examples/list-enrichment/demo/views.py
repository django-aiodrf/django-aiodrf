"""Batch and concurrent enrichment of list representations."""

import asyncio
from types import SimpleNamespace

from django.urls import path

from aiodrf import aio, serializers
from aiodrf.contrib.builtin.concurrent import ConcurrentListSerializer
from aiodrf.contrib.builtin.list_prefetch import PrefetchListSerializer
from aiodrf.response import Response
from aiodrf.views import APIView


async def local_lookup(number):
    await asyncio.sleep(0.01)
    return number * 10


class Batch(PrefetchListSerializer):
    async def aprefetch(self, instances):
        await asyncio.sleep(0.01)  # One simulated service batch.
        for instance in instances:
            instance.value = instance.number * 10


class BatchedItem(serializers.Serializer):
    number = serializers.IntegerField()
    value = serializers.IntegerField()

    class Meta:
        list_serializer_class = Batch


class Concurrent(ConcurrentListSerializer):
    max_concurrency = 2

    def get_item_serializer(self, instance):
        return ConcurrentItem(instance, context=dict(self.context))


class ConcurrentItem(serializers.Serializer):
    number = serializers.IntegerField()
    value = serializers.SerializerMethodField()

    async def get_value(self, instance):
        return await local_lookup(instance.number)

    class Meta:
        list_serializer_class = Concurrent


class Enrichment(APIView):
    serializer_class = BatchedItem

    async def get(self, request):
        rows = [SimpleNamespace(number=n) for n in range(4)]
        return Response(await aio.data(self.serializer_class(rows, many=True)))


urlpatterns = [
    path("batch/", Enrichment.as_view()),
    path("concurrent/", Enrichment.as_view(serializer_class=ConcurrentItem)),
]
