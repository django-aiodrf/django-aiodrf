"""Empty policy lists retain DRF factory hooks without policy classification."""

import threading

import pytest
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

from aiodrf import policies
from aiodrf.test import AsyncAPIRequestFactory
from aiodrf.views import APIView


@pytest.mark.parametrize("object_check", [False, True])
async def test_empty_builtin_permissions_do_not_need_classification(
    monkeypatch, object_check
):
    view = APIView(permission_classes=[])
    request = view.initialize_request(AsyncAPIRequestFactory().get("/"))

    def unexpected(*args):
        pytest.fail("An empty policy list has no execution mode or user access")

    monkeypatch.setattr(policies, "permissions_mode", unexpected)
    monkeypatch.setattr(policies, "reads_user", unexpected)
    if object_check:
        await view.acheck_object_permissions(request, object())
    else:
        await view.acheck_permissions(request)


async def test_custom_empty_factory_still_runs_in_worker():
    called = []

    class View(APIView):
        permission_classes = []

        def get_permissions(self):
            called.append(threading.get_ident())
            return []

    view = View()
    request = view.initialize_request(AsyncAPIRequestFactory().get("/"))
    await view.acheck_permissions(request)
    assert len(called) == 1
    assert called[0] != threading.get_ident()


async def test_falsey_custom_permission_collection_is_not_treated_as_empty():
    class Deny(BasePermission):
        def has_permission(self, request, view):
            return False

    class Collection(list):
        def __bool__(self):
            return False

    class View(APIView):
        authentication_classes = []

        def get_permissions(self):
            return Collection([Deny()])

    view = View()
    request = view.initialize_request(AsyncAPIRequestFactory().get("/"))
    with pytest.raises(PermissionDenied):
        await view.acheck_permissions(request)
