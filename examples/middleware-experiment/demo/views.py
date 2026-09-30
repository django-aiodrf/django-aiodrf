"""Async endpoint for comparing standard and opt-in middleware profiles."""

from django.conf import settings
from django.urls import path

from aiodrf.response import Response
from aiodrf.views import APIView


class Headers(APIView):
    async def get(self, request):
        return Response({"profile": settings.PROFILE})


urlpatterns = [path("headers/", Headers.as_view())]
