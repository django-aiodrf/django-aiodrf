"""Fastdrf's opt-in JSON transports through aiodrf's async view boundary."""

import decimal
import importlib
import json
import threading

import pytest
from asgiref.sync import iscoroutinefunction, sync_to_async
from django.utils.functional import lazy
from fastdrf.response import DataResponse
from rest_framework.settings import api_settings

from aiodrf.response import Response, StreamingArrayResponse, StreamingResponse
from aiodrf.test import AsyncAPIRequestFactory, count_hops
from aiodrf.views import APIView
from tests.test_imports import run


@pytest.fixture(params=[("pydantic", "PydanticJSON"), ("orjson", "ORJSON")])
def transport(request):
    package, prefix = request.param
    pytest.importorskip(package)
    parser = getattr(
        importlib.import_module(f"fastdrf.{package}.parsers"), prefix + "Parser"
    )
    renderer = getattr(
        importlib.import_module(f"fastdrf.{package}.renderers"), prefix + "Renderer"
    )
    return parser, renderer


async def render(response):
    if iscoroutinefunction(response.render):
        return (await response.render()).content
    return (await sync_to_async(response.render)()).content


@pytest.mark.parametrize("response_class", [Response, DataResponse])
async def test_selected_transport_parses_and_renders_async_views(
    transport, response_class
):
    parser, renderer = transport

    class Echo(APIView):
        authentication_classes = []
        permission_classes = []
        parser_classes = [parser]
        renderer_classes = [renderer]

        async def post(self, request):
            return response_class(await request.adata())

    payload = {"text": "ş🙂\u2028", "nested": [1, True, None]}
    request = AsyncAPIRequestFactory().post(
        "/", json.dumps(payload), content_type="application/json"
    )
    response = await Echo.as_view()(request)
    content = (
        await render(response) if isinstance(response, Response) else response.content
    )
    assert response.status_code == 200
    assert content == renderer().render(payload)
    assert response["Content-Type"].startswith("application/json")


@pytest.mark.parametrize("body", [b'{"incomplete":', b'{"x":NaN}', b'{"x":"\xff"}'])
async def test_invalid_input_is_a_rendered_400(transport, body):
    parser, renderer = transport

    class Echo(APIView):
        authentication_classes = []
        permission_classes = []
        parser_classes = [parser]
        renderer_classes = [renderer]

        async def post(self, request):
            return Response(await request.adata())

    request = AsyncAPIRequestFactory().post("/", body, content_type="application/json")
    response = await Echo.as_view()(request)
    assert response.status_code == 400
    assert response.data["detail"].code == "parse_error"
    assert "detail" in json.loads(await render(response))


@pytest.mark.parametrize("accept", ["application/json", "application/json; indent=2"])
async def test_response_keeps_native_values_and_indentation_semantics(
    transport, accept
):
    _, renderer = transport
    payload = {"amount": decimal.Decimal("1.50"), "text": "ş\u2028"}
    response = Response(payload)
    response.accepted_renderer = renderer()
    response.accepted_media_type = accept
    response.renderer_context = {}
    with count_hops() as hops:
        content = await render(response)
    assert hops.count == 0
    assert content == renderer().render(payload, accept, {})
    if accept == "application/json":
        expected = "1.50" if renderer.__name__ == "PydanticJSONRenderer" else 1.5
        assert json.loads(content)["amount"] == expected


async def test_lazy_values_and_renderer_overrides_run_in_worker(transport):
    _, renderer = transport
    threads = []

    def value():
        threads.append(threading.get_ident())
        return "lazy"

    class CustomRenderer(renderer):
        def get_indent(self, *args, **kwargs):
            threads.append(threading.get_ident())
            return super().get_indent(*args, **kwargs)

    for chosen, payload in [
        (renderer(), {"text": lazy(value, str)()}),
        (CustomRenderer(), {"text": "plain"}),
    ]:
        response = Response(payload)
        response.accepted_renderer = chosen
        response.accepted_media_type = "application/json"
        response.renderer_context = {}
        assert json.loads(await render(response))["text"] in {"lazy", "plain"}
    assert len(threads) >= 2
    assert all(thread != threading.get_ident() for thread in threads)


@pytest.mark.parametrize("response_class", [StreamingResponse, StreamingArrayResponse])
async def test_streaming_uses_selected_renderer(transport, response_class):
    _, renderer = transport
    payload = [{"n": 1}, {"n": 2}]
    response = response_class(payload, renderer=renderer())
    try:
        content = b"".join([chunk async for chunk in response])
    finally:
        await response.aclose()
    if response_class is StreamingArrayResponse:
        assert json.loads(content) == payload
    else:
        assert [json.loads(line) for line in content.splitlines()] == payload


def test_global_settings_can_select_transport_without_import_cycle(transport):
    parser, renderer = transport
    result = run(f'''
        import django
        from django.conf import settings
        settings.configure(SECRET_KEY="test", INSTALLED_APPS=[], REST_FRAMEWORK={{
            "DEFAULT_PARSER_CLASSES": ["{parser.__module__}.{parser.__name__}"],
            "DEFAULT_RENDERER_CLASSES": ["{renderer.__module__}.{renderer.__name__}"],
        }})
        django.setup()
        from aiodrf.views import APIView
        assert APIView.parser_classes[0].__name__ == "{parser.__name__}"
        assert APIView.renderer_classes[0].__name__ == "{renderer.__name__}"
        from fastdrf.settings import fastdrf_settings
        assert fastdrf_settings.SERIALIZER_BACKEND == "drf"
    ''')
    assert result.returncode == 0, result.stderr


def test_installing_transports_does_not_select_them(transport):
    parser, renderer = transport
    assert parser not in api_settings.DEFAULT_PARSER_CLASSES
    assert renderer not in api_settings.DEFAULT_RENDERER_CLASSES


async def test_custom_parser_runs_once_in_worker(transport):
    parser, renderer = transport
    threads = []

    class CustomParser(parser):
        def parse(self, *args, **kwargs):
            threads.append(threading.get_ident())
            return super().parse(*args, **kwargs)

    class Echo(APIView):
        authentication_classes = []
        permission_classes = []
        parser_classes = [CustomParser]
        renderer_classes = [renderer]

        async def post(self, request):
            value = await request.adata()
            assert await request.adata() is value
            return Response(value)

    request = AsyncAPIRequestFactory().post(
        "/", b'{"n":1}', content_type="application/json"
    )
    response = await Echo.as_view()(request)
    assert json.loads(await render(response)) == {"n": 1}
    assert len(threads) == 1
    assert threads[0] != threading.get_ident()
