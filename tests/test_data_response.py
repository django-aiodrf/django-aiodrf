"""``DataResponse``: DRF's rendered content in Django's ``HttpResponse``, opt-in."""

import gc
import threading
import weakref

import pytest
from django.core.asgi import get_asgi_application
from django.http import HttpResponse
from django.template.response import ContentNotRenderedError
from django.test import override_settings
from django.urls import path
from rest_framework import serializers as drf
from rest_framework import views as drf_views
from rest_framework.exceptions import NotFound
from rest_framework.renderers import (
    BrowsableAPIRenderer,
    JSONRenderer,
    StaticHTMLRenderer,
)

from aiodrf.contrib.msgspec.renderers import MsgspecJSONRenderer
from aiodrf.response import DataResponse, Response
from aiodrf.test import AsyncAPIClient, count_hops
from aiodrf.views import APIView
from tests.base import both_transports
from tests.test_response_release import _serve, freed_by_reference_counting

loop_threads = set()
render_threads = []


class Vector:
    """A value DRF's encoder converts by calling it (``tolist()``)."""

    def tolist(self):
        render_threads.append(threading.get_ident())
        return [1, 2]


PAYLOADS = {
    "object": ({"rows": [{"id": 1, "name": "ä"}]}, None, None),
    "list": ([1, 2.5, None, True], None, None),
    "empty": (None, None, None),
    "created": ({"id": 7}, 201, {"Location": "/rows/7/", "X-Extra": "1"}),
    "no-content": (None, 204, None),
    "text": ("\u2028 separator", None, None),
}


def pair(renderers):
    """Two views that answer the same payloads: DRF's Response and DataResponse."""

    def view(response_class):
        class View(APIView):
            authentication_classes = []
            permission_classes = []
            renderer_classes = renderers

            async def get(self, request, name):
                data, status, headers = PAYLOADS[name]
                return response_class(data, status=status, headers=headers)

        return View.as_view()

    return view(Response), view(DataResponse)


json_drf, json_data = pair([JSONRenderer])
msgspec_drf, msgspec_data = pair([MsgspecJSONRenderer])
browsable_drf, browsable_data = pair([JSONRenderer, BrowsableAPIRenderer])


class Cookies(APIView):
    authentication_classes = []
    permission_classes = []
    renderer_classes = [JSONRenderer, BrowsableAPIRenderer]

    async def get(self, request):
        response = DataResponse({"ok": True}, headers={"X-Kept": "yes"})
        response.set_cookie("flavour", "plain")
        return response


class Lazy(APIView):
    authentication_classes = []
    permission_classes = []
    renderer_classes = [JSONRenderer]
    response_class = DataResponse

    async def get(self, request):
        loop_threads.add(threading.get_ident())
        return self.response_class({"vector": Vector()})


class LazyDRF(Lazy):
    response_class = Response


class Sync(APIView):
    authentication_classes = []
    permission_classes = []
    renderer_classes = [JSONRenderer]

    def get(self, request):
        return DataResponse({"sync": True}, status=202)


class Indented(APIView):
    authentication_classes = []
    permission_classes = []
    renderer_classes = [JSONRenderer]
    response_class = DataResponse

    def get_renderer_context(self):
        return {**super().get_renderer_context(), "indent": 2}

    async def get(self, request):
        return self.response_class({"rows": [1]}, headers={"X-Hook": "context"})


class IndentedDRF(Indented):
    response_class = Response


class Finalized(Indented):
    def get_renderer_context(self):
        return APIView.get_renderer_context(self)

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["X-Finalized"] = "yes"
        return response


class FinalizedDRF(Finalized):
    response_class = Response


views = []


class Kept(APIView):
    authentication_classes = []
    permission_classes = []

    async def get(self, request):
        views.append(weakref.ref(self))
        return DataResponse({"rows": [{"id": 1}]})


urlpatterns = [
    path("json/drf/<str:name>/", json_drf),
    path("json/data/<str:name>/", json_data),
    path("msgspec/drf/<str:name>/", msgspec_drf),
    path("msgspec/data/<str:name>/", msgspec_data),
    path("browsable/drf/<str:name>/", browsable_drf),
    path("browsable/data/<str:name>/", browsable_data),
    path("cookies/", Cookies.as_view()),
    path("lazy/", Lazy.as_view()),
    path("lazy-drf/", LazyDRF.as_view()),
    path("sync/", Sync.as_view()),
    path("kept/", Kept.as_view()),
    path("indented/data/", Indented.as_view()),
    path("indented/drf/", IndentedDRF.as_view()),
    path("finalized/data/", Finalized.as_view()),
    path("finalized/drf/", FinalizedDRF.as_view()),
]


def _answer(response):
    return response.status_code, response.content, dict(response.items())


urls = override_settings(ROOT_URLCONF=__name__)


@both_transports
class _DataResponseParity:
    async def answers(self, prefix, name, **kwargs):
        drf = await self.api("get", f"/{prefix}/drf/{name}/", **kwargs)
        data = await self.api("get", f"/{prefix}/data/{name}/", **kwargs)
        if type(data) is DataResponse:
            # Rendered, it keeps its content only, as Django's responses do.
            assert data.data is None
        else:
            assert data.data == drf.data
        return drf, data

    async def assert_same(self, prefix, name, **kwargs):
        drf, data = await self.answers(prefix, name, **kwargs)
        assert _answer(data) == _answer(drf)
        return data

    @urls
    async def test_json_renderers_answer_as_drfs_response(self):
        for prefix in ("json", "msgspec"):
            for name in PAYLOADS:
                response = await self.assert_same(prefix, name)
                assert type(response) is DataResponse, (prefix, name)

    @urls
    async def test_an_indented_json_answer_is_drfs(self):
        for prefix in ("json", "msgspec"):
            await self.assert_same(
                prefix, "object", HTTP_ACCEPT="application/json; indent=2"
            )

    @urls
    async def test_other_renderers_answer_with_drfs_response(self):
        # The page shows the request's own URL and form tokens.
        drf, data = await self.answers("browsable", "created", HTTP_ACCEPT="text/html")
        assert isinstance(data, Response)
        assert data.status_code == drf.status_code == 201
        headers = {**dict(data.items()), "Content-Length": None}
        assert headers == {**dict(drf.items()), "Content-Length": None}
        assert b"Django REST framework" in data.content
        await self.assert_same("browsable", "object")

    @urls
    async def test_headers_and_cookies_set_by_the_view_are_kept(self):
        for accept in ("application/json", "text/html"):
            response = await self.api("get", "/cookies/", HTTP_ACCEPT=accept)
            assert response.status_code == 200
            assert response["X-Kept"] == "yes"
            assert response.cookies["flavour"].value == "plain"

    @urls
    async def test_views_with_their_own_hooks_answer_as_drfs_response(self):
        # A finalize_response of the project's may change the data after
        # DRF's: that view gets DRF's response.
        for prefix, kind in (("indented", DataResponse), ("finalized", Response)):
            drf = await self.api("get", f"/{prefix}/drf/")
            data = await self.api("get", f"/{prefix}/data/")
            assert _answer(data) == _answer(drf)
            assert type(data) is kind

    @urls
    async def test_a_synchronous_handler_can_return_one(self):
        response = await self.api("get", "/sync/")
        assert response.status_code == 202
        assert response.content == b'{"sync":true}'
        assert response["Content-Type"] == "application/json"


@override_settings(ROOT_URLCONF=__name__)
async def test_values_drf_would_evaluate_render_in_a_worker():
    client = AsyncAPIClient()
    hops = {}
    for url in ("/lazy-drf/", "/lazy/"):
        render_threads.clear()
        with count_hops() as counter:
            response = await client.get(url)
        hops[url] = counter.count
        assert response.content == b'{"vector":[1,2]}'
        assert render_threads
        assert not loop_threads & set(render_threads)
    assert hops["/lazy/"] == hops["/lazy-drf/"]


@override_settings(ROOT_URLCONF=__name__)
async def test_plain_data_renders_without_a_hop_of_its_own():
    client = AsyncAPIClient()
    with count_hops() as drf:
        await client.get("/json/drf/object/")
    with count_hops() as data:
        await client.get("/json/data/object/")
    assert data.count == drf.count


class Row:
    """A payload value that can be watched, rendered by DRF's encoder."""

    def tolist(self):
        return [1]


class Watched(APIView):
    authentication_classes = []
    permission_classes = []
    renderer_classes = [JSONRenderer]

    async def get(self, request):
        row = Row()
        self.row = weakref.ref(row)
        return DataResponse({"row": row})


async def test_the_payload_goes_once_rendered():
    from aiodrf.test import AsyncAPIRequestFactory

    view = Watched.as_view()
    response = await view(AsyncAPIRequestFactory().get("/"))
    assert response.content == b'{"row":[1]}'
    assert response.data is None
    assert response.renderer_context["view"].row() is None


def test_it_is_djangos_response():
    response = DataResponse({"a": 1}, status=201)
    assert isinstance(response, HttpResponse)
    assert not isinstance(response, Response)
    assert response.data == {"a": 1}
    assert response.status_code == 201


@freed_by_reference_counting
@pytest.mark.django_db(transaction=True)
@override_settings(ROOT_URLCONF=__name__)
async def test_the_request_objects_go_without_the_cyclic_collector():
    application = get_asgi_application()
    assert await _serve(application, "/kept/") == 200
    views.clear()
    gc.collect()
    gc.disable()
    try:
        assert await _serve(application, "/kept/") == 200
        assert views[0]() is None
    finally:
        gc.enable()


# -- Review 2026-09-29 -------------------------------------------------------------


class OwnDataResponse(DataResponse):
    pass


class Subclassed(Lazy):
    response_class = OwnDataResponse


class Delegating(APIView):
    authentication_classes = []
    permission_classes = []
    renderer_classes = [JSONRenderer]

    async def get(self, request):
        # Another view's answer, rendered by that view already.
        return await Kept.as_view()(request._request)


class Failing(APIView):
    authentication_classes = []
    permission_classes = []
    renderer_classes = [JSONRenderer, StaticHTMLRenderer]
    response_class = DataResponse

    def get_exception_handler(self):
        def handler(exc, context):
            return self.response_class({"detail": str(exc.detail)}, status=404)

        return handler

    async def get(self, request):
        raise NotFound("gone")


class FailingDRF(Failing):
    response_class = Response


class DRFView(drf_views.APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        return DataResponse({"a": 1})


review_urls = [
    path("subclassed/", Subclassed.as_view()),
    path("delegating/", Delegating.as_view()),
    path("failing/data/", Failing.as_view()),
    path("failing/drf/", FailingDRF.as_view()),
    path("drf-view/", DRFView.as_view()),
]
urlpatterns += review_urls


@override_settings(ROOT_URLCONF=__name__)
async def test_a_subclass_renders_what_drf_would_evaluate_in_a_worker():
    render_threads.clear()
    response = await AsyncAPIClient().get("/subclassed/")
    assert response.content == b'{"vector":[1,2]}'
    assert type(response) is OwnDataResponse
    assert render_threads
    assert not loop_threads & set(render_threads)


@override_settings(ROOT_URLCONF=__name__)
async def test_an_answer_rendered_by_another_view_is_kept():
    response = await AsyncAPIClient().get("/delegating/")
    assert response.content == b'{"rows":[{"id":1}]}'
    assert response["Content-Type"] == "application/json"


@override_settings(ROOT_URLCONF=__name__)
async def test_an_exception_answer_keeps_drfs_exception_flag():
    client = AsyncAPIClient()
    drf = await client.get("/failing/drf/", HTTP_ACCEPT="text/html")
    data = await client.get("/failing/data/", HTTP_ACCEPT="text/html")
    assert (data.status_code, data.content) == (drf.status_code, drf.content)
    assert data.exception is True


def test_unrendered_content_is_refused_as_djangos_template_responses_do():
    response = DataResponse({"a": 1})
    with pytest.raises(ContentNotRenderedError):
        response.content  # noqa: B018
    with pytest.raises(ContentNotRenderedError):
        list(response)


@override_settings(ROOT_URLCONF=__name__)
async def test_a_view_that_cannot_render_it_fails_loudly():
    with pytest.raises(ContentNotRenderedError):
        await AsyncAPIClient().get("/drf-view/")


def test_the_constructor_takes_drfs_arguments():
    response = DataResponse(
        {"a": 1}, content_type="text/plain", headers={"Content-Type": "x/y", "X": "1"}
    )
    assert response["X"] == "1"
    with pytest.raises(AssertionError, match="Serializer instance"):
        DataResponse(drf.Serializer())


class Enveloping(APIView):
    authentication_classes = []
    permission_classes = []
    renderer_classes = [JSONRenderer]
    response_class = DataResponse

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response.data = {"envelope": response.data}
        return response

    async def get(self, request):
        return self.response_class({"a": 1})


class EnvelopingDRF(Enveloping):
    response_class = Response


urlpatterns += [
    path("enveloping/data/", Enveloping.as_view()),
    path("enveloping/drf/", EnvelopingDRF.as_view()),
]


@override_settings(ROOT_URLCONF=__name__)
async def test_a_finalize_response_of_the_projects_sees_drfs_response():
    client = AsyncAPIClient()
    drf_answer = await client.get("/enveloping/drf/")
    data_answer = await client.get("/enveloping/data/")
    assert data_answer.content == drf_answer.content == b'{"envelope":{"a":1}}'
