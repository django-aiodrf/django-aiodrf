"""
``get_object``'s object permissions, decided once per view class: a view
whose permissions are plain classes with synchronous
``has_object_permission`` runs DRF's ``check_object_permissions`` loop
without classifying the permission instances on each request; anything
else keeps the general path. The answers are DRF's either way.
"""

from unittest import mock

import pytest
from rest_framework import permissions

from aiodrf import generics, policies, views
from aiodrf.test import AsyncAPIRequestFactory
from tests.testapp.models import Author
from tests.testapp.serializers import AuthorSerializer

factory = AsyncAPIRequestFactory()


class OwnsNothing(permissions.BasePermission):
    message = "Not yours."

    def has_object_permission(self, request, view, obj):
        return obj.name != "Ada"


class AsyncOwner(permissions.BasePermission):
    async def ahas_object_permission(self, request, view, obj):
        return obj.name != "Ada"


class Detail(generics.RetrieveAPIView):
    queryset = Author.objects.all()
    serializer_class = AuthorSerializer
    authentication_classes = []
    permission_classes = [permissions.AllowAny, OwnsNothing]


class Operators(Detail):
    permission_classes = [permissions.AllowAny & OwnsNothing]


class Awaited(Detail):
    permission_classes = [AsyncOwner]


class OwnFactory(Detail):
    def get_permissions(self):
        return [OwnsNothing()]


def test_the_plan_is_for_plain_synchronous_permission_classes():
    assert views._object_permission_plan(Detail, tuple(Detail.permission_classes))
    for view_class in (Operators, Awaited, OwnFactory):
        assert not views._object_permission_plan(
            view_class, tuple(view_class.permission_classes)
        )


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("view_class", [Detail, Operators, Awaited, OwnFactory])
async def test_the_answers_are_drfs(view_class, worker_connections):
    ada = await Author.objects.acreate(name="Ada")
    bo = await Author.objects.acreate(name="Bo")
    view = view_class.as_view()
    denied = await view(factory.get("/"), pk=ada.pk)
    allowed = await view(factory.get("/"), pk=bo.pk)
    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert allowed.data == {"id": bo.pk, "name": "Bo"}
    if view_class is Detail:
        assert denied.data == {"detail": "Not yours."}


@pytest.mark.django_db(transaction=True)
async def test_a_planned_view_does_not_classify_instances(worker_connections):
    bo = await Author.objects.acreate(name="Bo")
    with mock.patch.object(
        policies, "permissions_mode", wraps=policies.permissions_mode
    ) as mode:
        response = await Detail.as_view()(factory.get("/"), pk=bo.pk)
    assert response.status_code == 200
    assert mode.call_count == 0


@pytest.mark.django_db(transaction=True)
async def test_a_method_assigned_to_a_permission_instance_is_honoured(
    worker_connections,
):
    # A permission given an async method on the instance takes the general
    # path, which awaits it.
    ada = await Author.objects.acreate(name="Ada")

    class Assigned(Detail):
        def get_permissions(self):
            permission = OwnsNothing()

            async def refuse(request, view, obj):
                return False

            permission.ahas_object_permission = refuse
            return [permission]

    response = await Assigned.as_view()(factory.get("/"), pk=ada.pk)
    assert response.status_code == 403
