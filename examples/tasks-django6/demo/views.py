"""Enqueue jobs through Django's built-in Tasks API."""

from django.urls import path

from aiodrf import aio, serializers
from aiodrf.response import Response
from aiodrf.views import APIView

from .tasks import async_double, double, queued


class Input(serializers.Serializer):
    value = serializers.IntegerField(min_value=0, max_value=100)
    asynchronous = serializers.BooleanField(default=False)


class Enqueue(APIView):
    queued = False

    async def post(self, request):
        data = Input(data=request.data)
        await aio.is_valid(data, raise_exception=True)
        values = data.validated_data
        job = (
            queued
            if self.queued
            else (async_double if values["asynchronous"] else double)
        )
        result = await job.aenqueue(values["value"])
        body = {"id": result.id, "status": result.status}
        if result.status == "SUCCESSFUL":
            body["value"] = result.return_value
        return Response(body, status=202 if self.queued else 200)


urlpatterns = [
    path("enqueue/", Enqueue.as_view()),
    path("queue/", Enqueue.as_view(queued=True)),
]
