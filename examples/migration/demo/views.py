"""Legacy ADRF endpoints exercised through the explicit import shim."""

from adrf import serializers
from adrf.views import APIView
from django.urls import path
from rest_framework.response import Response


class Output(serializers.Serializer):
    name = serializers.CharField()


class Legacy(APIView):
    async def get(self, request):
        return Response(await Output({"name": "legacy"}).adata)


urlpatterns = [path("legacy/", Legacy.as_view())]
