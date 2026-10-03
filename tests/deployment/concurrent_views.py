"""The same async HTTP enrichment with sequential, bounded or prefetched item execution."""

import asyncio
from itertools import batched

from aiodrf_asgi_lifespan.asgi import get_lifespan_state

from aiodrf import serializers
from aiodrf.contrib.builtin.concurrent import ConcurrentListSerializer
from aiodrf.contrib.builtin.list_prefetch import PrefetchListSerializer
from aiodrf.response import Response
from aiodrf.views import APIView
from tests.deployment.lifecycle import Resources
from tests.deployment.views import external_body


class ExternalItem(serializers.Serializer):
    index = serializers.IntegerField()
    status = serializers.SerializerMethodField()

    async def get_status(self, obj):
        return await external_body(self.context["resources"])


class ConcurrentItems(ConcurrentListSerializer):
    max_concurrency = 4

    def get_item_serializer(self, instance):
        return ExternalItem(instance, context=dict(self.context))


class ConcurrentExternalItem(ExternalItem):
    class Meta:
        list_serializer_class = ConcurrentItems


class PairedItems(ConcurrentItems):
    max_concurrency = 2


class PairedExternalItem(ExternalItem):
    class Meta:
        list_serializer_class = PairedItems


class Prefetched(serializers.Serializer):
    index = serializers.IntegerField()
    status = serializers.CharField(read_only=True)


class BulkPrefetch(PrefetchListSerializer):
    # One upstream request for the whole list, as against a bulk endpoint.
    async def aprefetch(self, instances):
        body = await external_body(self.context["resources"])
        for item in instances:
            item["status"] = body


class GatherPrefetch(PrefetchListSerializer):
    # One request per item, four at a time, in one step of the list.
    async def aprefetch(self, instances):
        resources = self.context["resources"]
        for group in batched(instances, 4):
            bodies = await asyncio.gather(*(external_body(resources) for _ in group))
            for item, body in zip(group, bodies, strict=True):
                item["status"] = body


class BulkPrefetched(Prefetched):
    class Meta:
        list_serializer_class = BulkPrefetch


class GatherPrefetched(Prefetched):
    class Meta:
        list_serializer_class = GatherPrefetch


class SequentialEnrichment(APIView):
    serializer_class = ExternalItem

    async def get(self, request):
        count = int(request.query_params.get("items", "16"))
        if not 1 <= count <= 32:
            return Response({"detail": "items must be 1..32"}, status=400)
        serializer = self.serializer_class(
            [{"index": index} for index in range(count)],
            many=True,
            context={"resources": get_lifespan_state(request, Resources)},
        )
        return Response(await serializer.adata())


class ConcurrentEnrichment(SequentialEnrichment):
    serializer_class = ConcurrentExternalItem
