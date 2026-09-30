"""
A closed response lets its request objects go by reference counting: DRF's
view, request and response refer to each other, and without the cycles the
cyclic collector no longer has to find them. What the response keeps stays
readable after ``close()``.
"""

import asyncio
import gc
import weakref

import pytest
from django.core.asgi import get_asgi_application
from django.test import override_settings
from django.urls import path

from aiodrf import aio, serializers
from aiodrf.response import Response
from aiodrf.settings import ASGIREF_VERSION
from aiodrf.test import AsyncAPIClient
from aiodrf.views import APIView
from tests.asgi_driver import http_scope
from tests.testapp.models import Author

views = []

# asgiref before 3.9 keeps the handler's frames in a caught exception's
# traceback (``Local.__getattr__``), itself a cycle through the request.
freed_by_reference_counting = pytest.mark.skipif(
    ASGIREF_VERSION < (3, 9), reason="asgiref < 3.9 keeps request frames in a cycle"
)


class Echo(APIView):
    authentication_classes = []
    permission_classes = []

    async def get(self, request):
        views.append(weakref.ref(self))
        return Response({"rows": [{"id": 1}]})


class Authors(serializers.ModelSerializer):
    class Meta:
        model = Author
        fields = ["id", "name"]


rows = []


class Listing(Echo):
    async def get(self, request):
        authors = [Author(id=index, name=f"author {index}") for index in range(3)]
        rows.extend(weakref.ref(author) for author in authors)
        data = await aio.data(Authors(authors, many=True))
        if "page" in request.query_params:
            return Response({"count": 3, "results": data})
        return Response(data)


class OwnHead(Echo):
    async def head(self, request):
        return Response()


urlpatterns = [
    path("echo/", Echo.as_view()),
    path("own-head/", OwnHead.as_view()),
    path("authors/", Listing.as_view()),
]


@override_settings(ROOT_URLCONF=__name__)
async def test_what_a_closed_response_keeps_stays_readable():
    response = await AsyncAPIClient().get("/echo/")
    assert response.data == {"rows": [{"id": 1}]}
    view = response.renderer_context["view"]
    request = response.renderer_context["request"]
    assert view.request is request
    assert request.parser_context["kwargs"] == {}
    assert request.accepted_renderer is response.accepted_renderer
    # Back-references only: to the response itself, and to the view.
    assert "response" not in response.renderer_context
    assert "response" not in vars(view)
    assert "view" not in request.parser_context


async def _serve(application, path):
    """One request through Django's ASGI handler, as a server sends it."""
    sent = []
    received = False

    async def receive():
        nonlocal received
        if received:
            await asyncio.Event().wait()
        received = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    path, _, query = path.partition("?")
    scope = http_scope(path)
    scope["query_string"] = query.encode()
    await application(scope, receive, send)
    return sent[0]["status"]


@freed_by_reference_counting
@override_settings(ROOT_URLCONF=__name__)
async def test_the_request_objects_go_without_the_cyclic_collector():
    # Django's test client keeps the response (``response.json`` refers back
    # to it); a server lets it go once it is sent.
    application = get_asgi_application()
    assert await _serve(application, "/echo/") == 200  # first-use caches
    views.clear()
    gc.collect()
    gc.disable()
    try:
        assert await _serve(application, "/echo/") == 200
        assert views[0]() is None
    finally:
        gc.enable()


@override_settings(ROOT_URLCONF=__name__)
async def test_a_head_the_view_defines_is_kept():
    response = await AsyncAPIClient().get("/own-head/")
    view = response.renderer_context["view"]
    assert view.head.__func__ is OwnHead.head


@freed_by_reference_counting
@pytest.mark.parametrize("path", ["/authors/", "/authors/?page=1"])
@override_settings(ROOT_URLCONF=__name__)
async def test_the_serialized_instances_go_without_the_cyclic_collector(path):
    # The list serializer (``ReturnList.serializer``) and its child refer to
    # each other and hold the instances.
    application = get_asgi_application()
    assert await _serve(application, path) == 200
    rows.clear()
    gc.collect()
    gc.disable()
    try:
        assert await _serve(application, path) == 200
        assert rows
        assert all(reference() is None for reference in rows)
    finally:
        gc.enable()


@override_settings(ROOT_URLCONF=__name__)
async def test_a_closed_responses_serializer_still_answers():
    response = await AsyncAPIClient().get("/authors/")
    serializer = response.data.serializer
    assert serializer.child.parent == serializer
    assert list(serializer.child.fields) == ["id", "name"]  # built again
    assert response.data == [
        {"id": index, "name": f"author {index}"} for index in range(3)
    ]
