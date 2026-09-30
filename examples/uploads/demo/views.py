"""Multipart validation and explicit storage work outside the event loop."""

from django.core.files.storage import default_storage
from django.urls import path
from rest_framework.parsers import MultiPartParser

from aiodrf import aio, serializers
from aiodrf.response import Response
from aiodrf.utils import run_sync
from aiodrf.views import APIView


class Upload(serializers.Serializer):
    file = serializers.FileField()

    def validate_file(self, value):
        if value.size > 1048576:
            raise serializers.ValidationError("Files are limited to 1 MiB.")
        return value


class UploadView(APIView):
    parser_classes = [MultiPartParser]

    async def post(self, request):
        serializer = Upload(data=request.data)
        await aio.is_valid(serializer, raise_exception=True)
        uploaded = serializer.validated_data["file"]
        name = await run_sync(default_storage.save)(uploaded.name, uploaded)
        return Response({"name": name, "size": uploaded.size}, status=201)


urlpatterns = [path("upload/", UploadView.as_view())]
