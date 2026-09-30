"""aiodrf.W008: an async-only policy on DRF's base class, used by a DRF view."""

import types
from unittest import mock

from django.core.checks import Tags, run_checks
from django.test import override_settings
from django.urls import path
from rest_framework import authentication as drf_authentication
from rest_framework import permissions as drf_permissions
from rest_framework import throttling as drf_throttling
from rest_framework import views as drf_views
from rest_framework import viewsets as drf_viewsets
from rest_framework.decorators import action
from rest_framework.decorators import api_view as drf_api_view
from rest_framework.decorators import permission_classes as drf_permission_classes
from rest_framework.response import Response as DRFResponse
from rest_framework.routers import SimpleRouter

from aiodrf import authentication, checks, permissions, throttling
from aiodrf.response import Response
from aiodrf.views import APIView


class AsyncOnlyPermission(drf_permissions.BasePermission):
    async def ahas_permission(self, request, view):
        return False


class AsyncOnlyObjectPermission(drf_permissions.BasePermission):
    async def ahas_object_permission(self, request, view, obj):
        return False


class AsyncOnlyAuthentication(drf_authentication.BaseAuthentication):
    async def aauthenticate(self, request):
        return None


class AsyncOnlyThrottle(drf_throttling.BaseThrottle):
    async def aallow_request(self, request, view):
        return False


class BothMembers(drf_permissions.BasePermission):
    def has_permission(self, request, view):
        return False

    async def ahas_permission(self, request, view):
        return False


class SyncInABase(BothMembers):
    # The async member is closer, but DRF calls BothMembers.has_permission.
    async def ahas_permission(self, request, view):
        return True


class OnAiodrfsBases(permissions.BasePermission):
    async def ahas_permission(self, request, view):
        return False

    async def ahas_object_permission(self, request, view, obj):
        return False


class AuthenticationOnAiodrfsBase(authentication.BaseAuthentication):
    async def aauthenticate(self, request):
        return None


class ThrottleOnAiodrfsBase(throttling.BaseThrottle):
    async def aallow_request(self, request, view):
        return True


class DRFView(drf_views.APIView):
    def get(self, request):
        return DRFResponse({})


class AiodrfView(APIView):
    async def get(self, request):
        return Response({})


def drf_view(**policies):
    return type("Legacy", (DRFView,), policies)


def aiodrf_view(**policies):
    return type("Modern", (AiodrfView,), policies)


def w008(*patterns):
    conf = types.ModuleType("urls")
    conf.urlpatterns = list(patterns)
    with override_settings(ROOT_URLCONF=conf):
        return [
            message
            for message in checks.check_async_only_policies(app_configs=None)
            if message.id == "aiodrf.W008"
        ]


def test_an_async_only_permission_in_a_drf_view_is_reported():
    view = drf_view(permission_classes=[AsyncOnlyPermission])
    [message] = w008(path("a/", view.as_view()))
    assert message.obj is AsyncOnlyPermission
    assert "ahas_permission" in message.msg
    assert "has_permission" in message.msg
    assert "every request is allowed" in message.msg
    assert "Legacy" in message.msg
    assert "aiodrf.permissions.BasePermission" in message.hint


def test_each_pair_on_drfs_base_classes_is_reported():
    view = drf_view(
        authentication_classes=[AsyncOnlyAuthentication],
        permission_classes=[AsyncOnlyObjectPermission],
        throttle_classes=[AsyncOnlyThrottle],
    )
    messages = {
        message.obj: message.msg for message in w008(path("a/", view.as_view()))
    }
    assert messages.keys() == {
        AsyncOnlyAuthentication,
        AsyncOnlyObjectPermission,
        AsyncOnlyThrottle,
    }
    assert "`ahas_object_permission`" in messages[AsyncOnlyObjectPermission]
    assert "every object is allowed" in messages[AsyncOnlyObjectPermission]
    assert "`aauthenticate`" in messages[AsyncOnlyAuthentication]
    assert "NotImplementedError" in messages[AsyncOnlyAuthentication]
    assert "`aallow_request`" in messages[AsyncOnlyThrottle]
    assert "NotImplementedError" in messages[AsyncOnlyThrottle]


def test_composed_permissions_are_looked_into():
    # DRF's OR asks both operands: this one always allows.
    view = drf_view(
        permission_classes=[drf_permissions.IsAdminUser | AsyncOnlyPermission]
    )
    [message] = w008(path("a/", view.as_view()))
    assert message.obj is AsyncOnlyPermission


def test_policies_given_to_as_view_and_to_actions_are_reported():
    class Things(drf_viewsets.ViewSet):
        def list(self, request):
            return DRFResponse([])

        @action(detail=False, permission_classes=[AsyncOnlyObjectPermission])
        def recent(self, request):
            return DRFResponse([])

    router = SimpleRouter()
    router.register("things", Things, basename="things")
    messages = w008(
        path("a/", DRFView.as_view(permission_classes=[AsyncOnlyPermission])),
        *router.urls,
    )
    assert {message.obj for message in messages} == {
        AsyncOnlyPermission,
        AsyncOnlyObjectPermission,
    }


def test_drfs_function_views_are_reported():
    @drf_api_view(["GET"])
    @drf_permission_classes([AsyncOnlyPermission])
    def legacy(request):
        return DRFResponse({})

    [message] = w008(path("a/", legacy))
    assert message.obj is AsyncOnlyPermission


def test_the_rest_framework_defaults_of_drf_views_are_reported():
    # DRF's APIView reads DEFAULT_PERMISSION_CLASSES when it is defined.
    with mock.patch.object(
        drf_views.APIView, "permission_classes", [AsyncOnlyPermission]
    ):
        [message] = w008(path("a/", DRFView.as_view()))
    assert message.obj is AsyncOnlyPermission


def test_a_class_used_by_several_drf_views_is_reported_once():
    first = drf_view(permission_classes=[AsyncOnlyPermission])
    second = drf_view(permission_classes=[AsyncOnlyPermission])
    [message] = w008(path("a/", first.as_view()), path("b/", second.as_view()))
    assert message.obj is AsyncOnlyPermission


def test_aiodrf_views_await_the_async_member():
    view = aiodrf_view(
        authentication_classes=[AsyncOnlyAuthentication],
        permission_classes=[AsyncOnlyPermission, AsyncOnlyObjectPermission],
        throttle_classes=[AsyncOnlyThrottle],
    )
    assert w008(path("a/", view.as_view())) == []


def test_classes_drf_views_can_call_are_not_reported():
    view = drf_view(
        authentication_classes=[
            AuthenticationOnAiodrfsBase,
            drf_authentication.SessionAuthentication,
        ],
        permission_classes=[
            BothMembers,
            SyncInABase,
            OnAiodrfsBases,
            drf_permissions.IsAuthenticated,
        ],
        throttle_classes=[ThrottleOnAiodrfsBase, drf_throttling.AnonRateThrottle],
    )
    assert w008(path("a/", view.as_view())) == []


def test_the_check_runs_with_the_url_checks():
    view = drf_view(permission_classes=[AsyncOnlyPermission])
    conf = types.ModuleType("urls")
    conf.urlpatterns = [path("a/", view.as_view())]
    with override_settings(ROOT_URLCONF=conf):
        messages = run_checks(tags=[Tags.urls])
    assert "aiodrf.W008" in {message.id for message in messages}


class AsyncOnlyOnAllowAny(drf_permissions.AllowAny):
    async def ahas_permission(self, request, view):
        return False


class AsyncOnlyOnIsAuthenticated(drf_permissions.IsAuthenticated):
    async def ahas_permission(self, request, view):
        return False


def test_an_async_only_policy_on_a_drf_subclass_is_reported():
    # DRF views call AllowAny.has_permission: every request is allowed, and
    # ``ahas_permission`` never runs.
    view = drf_view(
        permission_classes=[AsyncOnlyOnAllowAny, AsyncOnlyOnIsAuthenticated]
    )
    messages = {message.obj: message for message in w008(path("a/", view.as_view()))}
    assert messages.keys() == {AsyncOnlyOnAllowAny, AsyncOnlyOnIsAuthenticated}
    assert "AllowAny.has_permission" in messages[AsyncOnlyOnAllowAny].msg
    assert "IsAuthenticated.has_permission" in messages[AsyncOnlyOnIsAuthenticated].msg


def test_a_urlconf_including_itself_is_walked_once_per_route():
    from itertools import islice

    from django.urls import include

    conf = types.ModuleType("urls")
    first = drf_view(permission_classes=[AsyncOnlyPermission])
    shared = types.ModuleType("shared")
    shared.urlpatterns = [path("b/", drf_view().as_view())]
    conf.urlpatterns = [
        path("a/", first.as_view()),
        path("again/", include(conf)),  # itself
        path("one/", include(shared)),
        path("two/", include(shared)),  # the same resolver on another route
    ]
    with override_settings(ROOT_URLCONF=conf):
        found = list(islice(checks._url_views(drf_views.APIView), 50))
    assert len(found) == 3
