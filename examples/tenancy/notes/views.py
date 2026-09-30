"""Tenant-selected worker connections also serve Django's async ORM methods."""

from rest_framework import serializers

from aiodrf.response import Response
from aiodrf.views import APIView
from aiodrf.viewsets import ModelViewSet

from .models import Note


class NoteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Note
        fields = ["id", "text"]


class Notes(ModelViewSet):
    queryset = Note.objects.order_by("pk")
    serializer_class = NoteSerializer


class Texts(APIView):
    async def get(self, request):
        texts = [
            text
            async for text in Note.objects.order_by("pk").values_list("text", flat=True)
        ]
        return Response({"tenant": request.tenant.schema_name, "texts": texts})
