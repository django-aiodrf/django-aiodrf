"""Access typed application resources from async request handlers."""

from aiodrf_asgi_lifespan.asgi import get_lifespan_state
from django.urls import path

from aiodrf.response import Response
from aiodrf.views import APIView

from .lifecycle import Resources


class Resource(APIView):
    async def get(self, request):
        resources = get_lifespan_state(request, Resources)
        response = await resources.http.get("/value")
        response.raise_for_status()
        return Response(response.json())


urlpatterns = [path("resource/", Resource.as_view())]
