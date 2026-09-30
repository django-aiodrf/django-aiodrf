"""Async view hooks, validation and ordinary DRF coexistence."""

from django.urls import path
from rest_framework.views import APIView as SyncAPIView

from aiodrf import aio, generics, serializers
from aiodrf.decorators import api_view
from aiodrf.response import Response


class Input(serializers.Serializer):
    name = serializers.CharField(max_length=40)

    async def avalidate_name(self, value):
        if value.casefold() == "reserved":
            raise serializers.ValidationError("This name is reserved.")
        return value


class Echo(generics.GenericAPIView):
    serializer_class = Input

    async def aget_serializer_context(self):
        return {**await super().aget_serializer_context(), "example": "basics"}

    async def afinalize_response(self, request, response, *args, **kwargs):
        response = await super().afinalize_response(request, response, *args, **kwargs)
        response["X-Example"] = "basics"
        return response

    async def post(self, request):
        serializer = await self.aget_serializer(data=request.data)
        await aio.is_valid(serializer, raise_exception=True)
        return Response(serializer.validated_data)


class UnchangedDRF(SyncAPIView):
    def get(self, request):
        return Response({"transport": "sync DRF"})


@api_view(["GET"])
async def decorated(request):
    return Response({"transport": "async decorator"})


urlpatterns = [
    path("echo/", Echo.as_view()),
    path("sync/", UnchangedDRF.as_view()),
    path("decorated/", decorated),
]
