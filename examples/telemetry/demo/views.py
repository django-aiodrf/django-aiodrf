"""Request endpoints with opt-in OpenTelemetry phase spans."""

from django.urls import path

from aiodrf.contrib.opentelemetry import TracingMixin
from aiodrf.response import Response
from aiodrf.views import APIView


class Traced(TracingMixin, APIView):
    async def get(self, request):
        return Response({"traced": True})


urlpatterns = [path("traced/", Traced.as_view())]
