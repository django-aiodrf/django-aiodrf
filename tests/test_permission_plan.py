"""
The permission plan: what ``acheck_permissions`` decides once per view class
and permission classes, and what keeps DRF's step-by-step path.
"""

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import User
from rest_framework import permissions

from aiodrf import views
from aiodrf.response import Response
from aiodrf.test import AsyncAPIRequestFactory, count_hops
from aiodrf.utils import register_pure
from aiodrf.views import APIView

factory = AsyncAPIRequestFactory()


async def get(self, request):
    return Response({"ok": True})


def view_class(*classes, **attributes):
    return type(
        "Checked",
        (APIView,),
        {
            "authentication_classes": [],
            "permission_classes": list(classes),
            "get": get,
            **attributes,
        },
    )


@pytest.mark.parametrize(
    ("permission", "reads_user"),
    [
        (permissions.AllowAny, False),
        (permissions.IsAuthenticated, True),
        (permissions.IsAdminUser, True),
        (permissions.IsAuthenticatedOrReadOnly, True),
    ],
)
def test_drfs_pure_permissions_are_planned(permission, reads_user):
    cls = view_class(permission)
    assert views._permission_plan(cls, (permission,)) is reads_user


class Async(permissions.BasePermission):
    async def ahas_permission(self, request, view):
        return True


class Constructed(permissions.BasePermission):
    def __init__(self):
        self.seen = True


class Impure(permissions.BasePermission):
    def has_permission(self, request, view):
        return True


@pytest.mark.parametrize(
    "classes",
    [
        (permissions.IsAuthenticated | permissions.AllowAny,),
        (Async,),
        (Constructed,),
        (Impure,),
    ],
    ids=["operator", "async", "constructor", "not pure"],
)
def test_what_the_plan_leaves_to_drfs_path(classes):
    assert views._permission_plan(view_class(*classes), classes) is None


def test_a_view_with_its_own_get_permissions_is_not_planned():
    cls = view_class(
        permissions.AllowAny, get_permissions=lambda self: [permissions.AllowAny()]
    )
    assert views._permission_plan(cls, (permissions.AllowAny,)) is None


def test_a_view_with_its_own_permission_denied_is_not_planned():
    cls = view_class(
        permissions.IsAuthenticated,
        permission_denied=lambda self, request, message=None, code=None: None,
    )
    assert views._permission_plan(cls, (permissions.IsAuthenticated,)) is None


async def test_a_planned_check_costs_no_hop():
    view = view_class(permissions.AllowAny).as_view()
    with count_hops() as hops:
        assert (await view(factory.get("/"))).status_code == 200
    assert hops.count == 0


async def test_a_planned_denial_is_drfs():
    from rest_framework.authentication import BasicAuthentication

    # Without authenticators DRF's ``permission_denied`` answers 403; with
    # one that did not authenticate, 401.
    view = view_class(permissions.IsAuthenticated).as_view()
    response = await view(factory.get("/"))
    assert response.status_code == 403
    assert response.data["detail"].code == "permission_denied"
    view = view_class(
        permissions.IsAuthenticated, authentication_classes=[BasicAuthentication]
    ).as_view()
    response = await view(factory.get("/"))
    assert response.status_code == 401
    assert response.data["detail"].code == "not_authenticated"


@pytest.mark.django_db(transaction=True)
async def test_a_planned_check_reads_the_authenticated_user():
    from rest_framework.authentication import BasicAuthentication

    await sync_to_async(User.objects.create_user)("u", password="p")
    view = view_class(
        permissions.IsAuthenticated, authentication_classes=[BasicAuthentication]
    ).as_view()
    request = factory.get("/", HTTP_AUTHORIZATION="Basic dTpw")
    response = await view(request)
    assert response.status_code == 200, response.data
    assert request.user.username == "u"


async def test_a_declaration_made_later_is_seen_by_the_next_request():
    class Later(permissions.BasePermission):
        def has_permission(self, request, view):
            return True

    view = view_class(Later).as_view()
    with count_hops() as hops:
        await view(factory.get("/"))
    assert hops.count == 1
    register_pure(Later)
    with count_hops() as hops:
        await view(factory.get("/"))
    assert hops.count == 0
