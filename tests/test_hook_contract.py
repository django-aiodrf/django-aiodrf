"""Metadata placement, finalization bridges and unsupported async hooks."""

import asyncio
import threading

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
from django.urls import path
from django.utils.asyncio import async_unsafe
from rest_framework.metadata import SimpleMetadata
from rest_framework.parsers import JSONParser
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response as DRFResponse

from aiodrf.request import Request
from aiodrf.response import Response, StreamingResponse
from aiodrf.test import APIClient, AsyncAPIClient, AsyncAPIRequestFactory
from aiodrf.utils import count_hops, run_sync
from aiodrf.views import APIView
from aiodrf.viewsets import ViewSet
from tests.testapp.models import Author


class Public(APIView):
    authentication_classes = []
    permission_classes = []

    async def get(self, request):
        return DRFResponse({"ok": True})


@pytest.mark.django_db(transaction=True)
async def test_metadata_construction_and_execution_share_one_worker_without_replay():
    await Author.objects.acreate(name="Ursula")
    calls = []

    class Metadata(SimpleMetadata):
        @async_unsafe("metadata constructor on loop")
        def __init__(self):
            calls.append(("init", threading.get_ident()))
            self.count = Author.objects.count()

        def determine_metadata(self, request, view):
            calls.append(("metadata", threading.get_ident()))
            return {**super().determine_metadata(request, view), "authors": self.count}

    with count_hops() as hops:
        response = await Public.as_view(metadata_class=Metadata)(
            AsyncAPIRequestFactory().options("/")
        )
    assert response.status_code == 200
    assert response.data["authors"] == 1
    assert [event for event, _ in calls] == ["init", "metadata"]
    assert calls[0][1] == calls[1][1] != threading.get_ident()
    assert hops.count == 1


@pytest.mark.parametrize("disabled", [False, True])
async def test_ordinary_and_disabled_metadata(disabled):
    response = await Public.as_view(
        metadata_class=None if disabled else SimpleMetadata
    )(AsyncAPIRequestFactory().options("/"))
    assert response.status_code == (405 if disabled else 200)
    if not disabled:
        assert response.data == {
            "name": "Public",
            "description": "",
            "renders": ["application/json", "text/html"],
            "parses": [
                "application/json",
                "application/x-www-form-urlencoded",
                "multipart/form-data",
            ],
        }


@pytest.mark.parametrize("transport", ["sync", "async"])
@pytest.mark.parametrize("kind", ["sync", "async", "async_sync_name", "both"])
async def test_finalization_preserves_legacy_and_async_overrides_once(transport, kind):
    trace = []
    loop_thread = threading.get_ident()

    class View(Public):
        pass

    def finalize(self, request, response, *args, **kwargs):
        assert threading.get_ident() != loop_thread
        trace.append("sync")
        result = super(View, self).finalize_response(request, response, *args, **kwargs)
        result["X-Finalized"] = "sync"
        return result

    async def afinalize(self, request, response, *args, **kwargs):
        await asyncio.sleep(0)
        trace.append("async")
        result = await super(View, self).afinalize_response(
            request, response, *args, **kwargs
        )
        result["X-Finalized"] = "async"
        return result

    if kind in ("sync", "both"):
        View.finalize_response = finalize
    if kind in ("async", "both"):
        View.afinalize_response = afinalize
    if kind == "async_sync_name":
        View.finalize_response = afinalize
    with override_settings(ROOT_URLCONF=(path("", View.as_view()),)):
        response = (
            await AsyncAPIClient().get("/")
            if transport == "async"
            else await run_sync(APIClient().get)("/")
        )
    expected = "sync" if kind == "sync" else "async"
    assert trace == [expected]
    assert response["X-Finalized"] == expected
    assert response.data == {"ok": True}
    assert response.accepted_media_type == "application/json"


async def test_sync_caller_reaches_async_finalizer_without_recursion():
    calls = []

    class View(Public):
        async def afinalize_response(self, request, response, *args, **kwargs):
            calls.append("async")
            return await super().afinalize_response(request, response, *args, **kwargs)

    view = View()
    view.headers = view.default_response_headers
    request = view.initialize_request(AsyncAPIRequestFactory().get("/"))
    result = await run_sync(view.finalize_response)(request, DRFResponse({"ok": True}))
    assert calls == ["async"]
    assert isinstance(result, Response)
    assert result.accepted_renderer.format == "json"


@pytest.mark.parametrize("child_async", [False, True])
async def test_nearest_finalization_override_wins(child_async):
    calls = []

    class Parent(Public):
        def finalize_response(self, request, response, *args, **kwargs):
            calls.append("sync")
            return super().finalize_response(request, response, *args, **kwargs)

        async def afinalize_response(self, request, response, *args, **kwargs):
            calls.append("async")
            return await super().afinalize_response(request, response, *args, **kwargs)

    class Child(Parent):
        pass

    if child_async:
        Child.afinalize_response = Parent.afinalize_response
    else:
        Child.finalize_response = Parent.finalize_response
    await Child.as_view()(AsyncAPIRequestFactory().get("/"))
    assert calls == ["async" if child_async else "sync"]


async def test_default_finalizer_runs_custom_renderer_context_in_worker():
    class View(Public):
        @async_unsafe("renderer context on loop")
        def get_renderer_context(self):
            return {**super().get_renderer_context(), "custom": True}

    with count_hops() as hops:
        response = await View.as_view()(AsyncAPIRequestFactory().get("/"))
    assert response.renderer_context["custom"] is True
    assert hops.count == 1


async def test_finalizer_also_runs_for_handled_errors_and_propagates_its_own_failure():
    from rest_framework.exceptions import NotFound

    class View(Public):
        async def get(self, request):
            raise NotFound("missing")

        async def afinalize_response(self, request, response, *args, **kwargs):
            assert response.status_code == 404
            raise RuntimeError("finalizer refused")

    with pytest.raises(RuntimeError, match="finalizer refused"):
        await View.as_view()(AsyncAPIRequestFactory().get("/"))


@pytest.mark.parametrize("base", [Public, ViewSet])
@pytest.mark.parametrize(
    "hook",
    [
        "get_parsers",
        "get_serializer_class",
        "get_renderer_context",
        # DRF calls them to raise; an unawaited coroutine would let the
        # request through.
        "permission_denied",
        "throttled",
    ],
)
def test_unsupported_async_view_hooks_fail_during_url_construction(base, hook):
    async def unsupported(self):
        raise AssertionError("do not call unsupported hooks to inspect them")

    view = type("Unsupported", (base,), {hook: unsupported})
    args = ({"get": "list"},) if base is ViewSet else ()
    with pytest.raises(ImproperlyConfigured, match=rf"{hook} must be synchronous"):
        view.as_view(*args)


@pytest.mark.parametrize("hook", ["get_permissions", "permission_denied"])
def test_async_hooks_given_to_as_view_are_refused(hook):
    async def unsupported(*args, **kwargs):
        raise AssertionError("do not call unsupported hooks to inspect them")

    with pytest.raises(ImproperlyConfigured, match=rf"{hook} must be synchronous"):
        Public.as_view(**{hook: unsupported})


def test_a_hook_behind_a_property_is_refused():
    # What the property returns is known only once it runs.
    async def unsupported(*args, **kwargs):
        raise AssertionError("do not call unsupported hooks to inspect them")

    view = type(
        "Hidden", (Public,), {"permission_denied": property(lambda self: unsupported)}
    )
    with pytest.raises(
        ImproperlyConfigured, match="permission_denied must be a method"
    ):
        view.as_view()


class AsyncParser(JSONParser):
    async def parse(self, *args, **kwargs):
        return {}


class AsyncRenderer(JSONRenderer):
    async def render(self, *args, **kwargs):
        return b"{}"


@pytest.mark.parametrize(
    ("setting", "component", "hook"),
    [
        ("parser_classes", AsyncParser, "parse"),
        ("renderer_classes", AsyncRenderer, "render"),
    ],
)
def test_unsupported_static_components_fail_before_construction(
    setting, component, hook
):
    with pytest.raises(ImproperlyConfigured, match=rf"{hook} must be synchronous"):
        Public.as_view(**{setting: [component]})


async def test_dynamic_parser_and_renderer_are_also_rejected_without_coroutine_leaks():
    request = Request(
        AsyncAPIRequestFactory().post("/", {"x": 1}, format="json"),
        parsers=[AsyncParser()],
    )
    with pytest.raises(ImproperlyConfigured, match="parse must be synchronous"):
        await request.adata()
    response = Response({})
    response.accepted_renderer = AsyncRenderer()
    with pytest.raises(ImproperlyConfigured, match="render must be synchronous"):
        response.render()
    with pytest.raises(ImproperlyConfigured, match="render must be synchronous"):
        StreamingResponse([], renderer=AsyncRenderer())


def test_components_chosen_by_the_instance_are_left_to_the_request():
    # DRF's ``get_schema_view()`` passes ``renderer_classes=None``; its
    # ``SchemaView`` picks the renderers when it is constructed.
    assert callable(Public.as_view(renderer_classes=None))


def test_as_view_can_be_reached_from_an_instance_as_in_drf():
    # ``rest_framework.views.APIView.as_view`` is a classmethod.
    assert callable(Public().as_view())


def test_the_check_is_repeated_for_every_use_and_sees_instance_attributes():
    # The answer for a class is kept; an async hook set on an instance is not
    # a property of its class and is refused as well.
    for _ in range(2):
        response = Response({})
        response.accepted_renderer = AsyncRenderer()
        with pytest.raises(ImproperlyConfigured, match="render must be synchronous"):
            response.render()

    async def render(*args, **kwargs):
        return b"{}"

    renderer = JSONRenderer()
    renderer.render = render
    response = Response({})
    response.accepted_renderer = renderer
    with pytest.raises(ImproperlyConfigured, match="render must be synchronous"):
        response.render()


@pytest.mark.parametrize("authenticated", [False, True])
async def test_session_adapter_honors_explicit_middleware_user_over_stale_async_cache(
    authenticated,
):
    from django.contrib.auth.models import AnonymousUser, User
    from rest_framework.authentication import SessionAuthentication

    from aiodrf.authentication import asession_authenticate

    django_request = AsyncAPIRequestFactory().get("/")
    user = User(username="alice") if authenticated else AnonymousUser()
    django_request.user = user

    async def stale():
        raise AssertionError("an explicit middleware user is authoritative, as in DRF")

    django_request.auser = stale
    result = await asession_authenticate(
        SessionAuthentication(), Request(django_request)
    )
    assert result == ((user, None) if authenticated else None)


@pytest.mark.django_db(transaction=True)
async def test_success_headers_override_runs_off_the_loop_after_async_representation():
    from rest_framework import serializers

    from aiodrf import generics

    class AuthorWithCount(serializers.ModelSerializer):
        books = serializers.SerializerMethodField()

        class Meta:
            model = Author
            fields = ["id", "name", "books"]

        async def get_books(self, author):
            return await author.books.acount()

    class Create(generics.CreateAPIView):
        authentication_classes = []
        permission_classes = []
        queryset = Author.objects.all()
        serializer_class = AuthorWithCount

        @async_unsafe("success headers on loop")
        def get_success_headers(self, data):
            return {"X-Authors": str(Author.objects.count())}

    request = AsyncAPIRequestFactory().post("/", {"name": "Ursula"}, format="json")
    response = await Create.as_view()(request)
    assert response.status_code == 201
    assert response["X-Authors"] == "1"
    assert response.data["books"] == 0
