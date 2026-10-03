"""
A renderer registered with django-fastdrf's ``register_data_renderer`` is
the project's code: aiodrf renders it in a thread unless it is declared pure.
"""

import inspect
import threading

import pytest
from asgiref.sync import sync_to_async
from fastdrf.registry import register_data_renderer
from fastdrf.testing import isolated_registry
from rest_framework import renderers

from aiodrf import views
from aiodrf.response import Response, StreamingResponse
from aiodrf.test import AsyncAPIRequestFactory
from aiodrf.utils import register_pure
from tests.testapp.models import Author

SEEN = []


class Counting(renderers.BaseRenderer):
    media_type = "application/json"
    format = "json"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        SEEN.append(threading.current_thread() is threading.main_thread())
        # The project's code may query: on the event loop it would raise.
        return b'{"n": %d}' % Author.objects.count()


class View(views.APIView):
    authentication_classes = []
    permission_classes = []
    renderer_classes = [Counting]

    async def get(self, request):
        return Response({"a": 1})


@pytest.fixture(autouse=True)
def registered():
    SEEN.clear()
    with isolated_registry():
        register_data_renderer(Counting)
        yield


async def rendered(response):
    # As Django's async handler renders a response.
    if inspect.iscoroutinefunction(response.render):
        return (await response.render()).content
    return (await sync_to_async(response.render)()).content


@pytest.mark.django_db(transaction=True)
async def test_a_response_renders_in_a_thread(worker_connections):
    response = await View.as_view()(AsyncAPIRequestFactory().get("/"))
    assert await rendered(response) == b'{"n": 0}'
    assert SEEN == [False]


@pytest.mark.django_db(transaction=True)
async def test_a_stream_renders_in_a_thread(worker_connections):
    async def items():
        yield {"a": 1}

    response = StreamingResponse(items(), renderer=Counting())
    assert b"".join([chunk async for chunk in response]).strip() == b'{"n": 0}'
    assert SEEN == [False]


def test_a_renderer_declared_pure_renders_on_the_loop():
    class Pure(renderers.BaseRenderer):
        media_type = "application/json"

        def render(self, data, accepted_media_type=None, renderer_context=None):
            return b"{}"

    from aiodrf.response import _Render, _render_inline

    register_pure(Pure)
    register_data_renderer(Pure)
    response = Response({"a": 1})
    response.accepted_renderer = Pure()
    response.accepted_media_type = "application/json"
    response.renderer_context = {}
    assert _Render().__get__(response).__func__ is _render_inline
