"""
The request plan: a view that leaves its request lifecycle to the framework
runs the steps DRF runs, building the same objects and setting the same state,
decided once per class and checked against the view's configuration on every
request.
"""

import pytest
from django.contrib.auth.models import User
from rest_framework import permissions
from rest_framework.parsers import FormParser, JSONParser
from rest_framework.renderers import BrowsableAPIRenderer, JSONRenderer
from rest_framework.test import force_authenticate

from aiodrf import views, viewsets
from aiodrf.response import Response
from aiodrf.test import AsyncAPIRequestFactory
from aiodrf.views import APIView

factory = AsyncAPIRequestFactory()
seen = []


class Planned(APIView):
    authentication_classes = []
    permission_classes = []
    throttle_classes = []
    parser_classes = [JSONParser, FormParser]
    renderer_classes = [JSONRenderer, BrowsableAPIRenderer]

    async def get(self, request, *args, **kwargs):
        seen.append((self, request))
        return Response({"ok": True})


class Generic(Planned):
    # Overriding a step of the lifecycle puts the view on the generic path.
    def get_parser_context(self, http_request):
        return super().get_parser_context(http_request)


class Deny(permissions.BasePermission):
    def has_permission(self, request, view):
        return False


def test_the_plan_is_for_views_that_leave_the_lifecycle_to_the_framework():
    assert views._request_plan(Planned()) is not None
    assert views._request_plan(Generic()) is None
    # Configuration of the instance (``as_view()`` arguments).
    assert views._request_plan(Planned(renderer_classes=[JSONRenderer])) is None


async def _state(view_class, request, **kwargs):
    seen.clear()
    response = await view_class.as_view()(request, **kwargs)
    response.render()
    ((view, drf_request),) = seen
    return {
        "status": response.status_code,
        # The browsable API's page names the view's class.
        "content": None
        if type(response.accepted_renderer) is BrowsableAPIRenderer
        else response.content,
        "headers": sorted(response.headers.items()),
        "renderer": type(response.accepted_renderer),
        "media_type": response.accepted_media_type,
        "renderer_context": sorted(response.renderer_context),
        "request_renderer": type(drf_request.accepted_renderer),
        "version": (drf_request.version, drf_request.versioning_scheme),
        "user": drf_request.user,
        "auth": drf_request.auth,
        "format_kwarg": view.format_kwarg,
        "negotiator": type(view._negotiator),
        "parsers": [type(parser) for parser in drf_request.parsers],
        "authenticators": list(drf_request.authenticators),
        "parser_context": sorted(drf_request.parser_context),
        "context_view": drf_request.parser_context["view"] is view,
    }


@pytest.mark.parametrize(
    ("path", "extra", "kwargs"),
    [
        ("/", {}, {}),
        ("/", {"HTTP_ACCEPT": "application/json"}, {}),
        ("/", {"HTTP_ACCEPT": "text/html"}, {}),
        ("/?format=api", {}, {}),
        ("/", {"HTTP_ACCEPT": "application/json; indent=2"}, {}),
        ("/", {}, {"format": "json"}),
    ],
)
async def test_planned_and_generic_views_answer_alike(path, extra, kwargs):
    planned = await _state(Planned, factory.get(path, **extra), **kwargs)
    generic = await _state(Generic, factory.get(path, **extra), **kwargs)
    assert planned == generic


async def test_an_unacceptable_request_is_refused_alike():
    for view_class in (Planned, Generic):
        response = await view_class.as_view()(factory.get("/", HTTP_ACCEPT="text/csv"))
        assert response.status_code == 406


async def test_class_configuration_changed_at_runtime_is_read_on_every_request(
    monkeypatch,
):
    view = Planned.as_view()
    assert (await view(factory.get("/"))).status_code == 200
    # DRF reads the classes on every request; so does the plan.
    monkeypatch.setattr(Planned, "permission_classes", [Deny])
    assert (await view(factory.get("/"))).status_code == 403
    monkeypatch.setattr(Planned, "permission_classes", [])
    monkeypatch.setattr(Planned, "renderer_classes", [BrowsableAPIRenderer])
    response = await view(factory.get("/", HTTP_ACCEPT="application/json"))
    assert response.status_code == 406


async def test_a_renderer_class_list_changed_in_place_takes_effect(monkeypatch):
    renderers = [JSONRenderer]
    monkeypatch.setattr(Planned, "renderer_classes", renderers)
    view = Planned.as_view()
    assert (await view(factory.get("/", HTTP_ACCEPT="text/html"))).status_code == 406
    renderers.append(BrowsableAPIRenderer)
    response = await view(factory.get("/", HTTP_ACCEPT="text/html"))
    assert response.status_code == 200


@pytest.mark.django_db
async def test_a_forced_user_is_authenticated_on_the_plan():
    user = User(username="ursula")
    request = factory.get("/")
    force_authenticate(request, user=user)
    state = await _state(Planned, request)
    assert state["user"] is user


async def test_a_viewset_keeps_its_action():
    class Actions(viewsets.ViewSet):
        authentication_classes = []
        permission_classes = []

        async def list(self, request):
            return Response({"action": self.action})

    response = await Actions.as_view({"get": "list"})(factory.get("/"))
    assert response.data == {"action": "list"}
