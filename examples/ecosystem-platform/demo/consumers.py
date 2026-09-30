"""DCRF consumes aiodrf permissions through their synchronous bridge."""

from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import ListModelMixin

from aiodrf.permissions import BasePermission

from .models import Record
from .views import RecordSerializer


class OpenCatalogue(BasePermission):
    async def ahas_permission(self, request, view):
        return await Record.objects.filter(title="public").aexists()


class Records(ListModelMixin, GenericAsyncAPIConsumer):
    queryset = Record.objects.order_by("pk")
    serializer_class = RecordSerializer
    permission_classes = [OpenCatalogue]
