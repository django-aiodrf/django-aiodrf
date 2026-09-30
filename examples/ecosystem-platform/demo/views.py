"""Normal generic CRUD beside an asynchronous request-context endpoint."""

import structlog
from django.http import HttpResponse
from rest_framework import serializers
from rest_framework.authentication import BasicAuthentication
from rest_framework.permissions import IsAuthenticated

from aiodrf.response import Response
from aiodrf.views import APIView
from aiodrf.viewsets import ModelViewSet

from .models import Record


class RecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = Record
        fields = ["id", "title"]


class Records(ModelViewSet):
    queryset = Record.objects.order_by("pk")
    serializer_class = RecordSerializer


class Ping(APIView):
    async def get(self, request):
        await structlog.get_logger(__name__).ainfo("ping")
        return Response({"ok": True, "request_id": getattr(request, "id", None)})


class Identity(APIView):
    authentication_classes = [BasicAuthentication]
    permission_classes = [IsAuthenticated]

    async def get(self, request):
        return Response({"username": request.user.get_username()})


async def diagnostic_page(request):
    return HttpResponse("<html><body>Local diagnostics</body></html>")
