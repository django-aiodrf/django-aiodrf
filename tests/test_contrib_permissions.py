"""
``aiodrf.contrib.permissions``: DRF's model-permission classes with an async
``has_permission`` that needs no thread hop when no permission is required.
The decisions are compared with DRF's own classes.
"""

import pytest
from django.contrib.auth.models import AnonymousUser, Permission, User
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import include, path
from rest_framework import permissions as drf_permissions
from rest_framework import serializers
from rest_framework import viewsets as drf_viewsets
from rest_framework.authentication import BasicAuthentication
from rest_framework.routers import DefaultRouter

from aiodrf import viewsets
from aiodrf.contrib import permissions
from aiodrf.test import AsyncAPIClient, count_hops
from tests.base import both_transports
from tests.testapp.models import Author


class AuthorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Author
        fields = ["id", "name"]


class ViewPermissions(drf_permissions.DjangoModelPermissions):
    perms_map = {
        **drf_permissions.DjangoModelPermissions.perms_map,
        "GET": ["%(app_label)s.view_%(model_name)s"],
    }


class AsyncViewPermissions(permissions.DjangoModelPermissions):
    perms_map = ViewPermissions.perms_map


PAIRS = {
    "model": (
        drf_permissions.DjangoModelPermissions,
        permissions.DjangoModelPermissions,
    ),
    "anon": (
        drf_permissions.DjangoModelPermissionsOrAnonReadOnly,
        permissions.DjangoModelPermissionsOrAnonReadOnly,
    ),
    "object": (
        drf_permissions.DjangoObjectPermissions,
        permissions.DjangoObjectPermissions,
    ),
    "view": (ViewPermissions, AsyncViewPermissions),
}


def viewset(base, permission):
    return type(
        f"{permission.__name__}ViewSet",
        (base,),
        {
            "queryset": Author.objects.order_by("id"),
            "serializer_class": AuthorSerializer,
            "authentication_classes": [BasicAuthentication],
            "permission_classes": [permission],
        },
    )


router = DefaultRouter()
for name, (drf_permission, aiodrf_permission) in PAIRS.items():
    router.register(
        f"drf-{name}", viewset(drf_viewsets.ModelViewSet, drf_permission), f"drf-{name}"
    )
    router.register(
        f"aiodrf-{name}",
        viewset(viewsets.ModelViewSet, aiodrf_permission),
        f"aiodrf-{name}",
    )


class QuerysetHook(viewsets.ModelViewSet):
    serializer_class = AuthorSerializer
    authentication_classes = [BasicAuthentication]
    permission_classes = [permissions.DjangoModelPermissions]

    async def aget_queryset(self):
        return Author.objects.order_by("id")


router.register("aqueryset", QuerysetHook, "aqueryset")

urlpatterns = [path("", include(router.urls))]
urls = override_settings(ROOT_URLCONF=__name__)


class Users:
    @classmethod
    def setUpTestData(cls):
        cls.author = Author.objects.create(name="Ursula")
        cls.users = {"nobody": User.objects.create_user("nobody", password="pw")}
        writer = User.objects.create_user("writer", password="pw")
        writer.user_permissions.add(
            *Permission.objects.filter(
                codename__in=["add_author", "view_author", "change_author"]
            )
        )
        cls.users["writer"] = writer
        inactive = User.objects.create_user("inactive", password="pw", is_active=False)
        inactive.user_permissions.add(*Permission.objects.filter(codename="add_author"))
        cls.users["inactive"] = inactive
        cls.users["admin"] = User.objects.create_superuser("admin", password="pw")

    def login(self, name):
        if name == "anonymous":
            self.client.credentials()
        else:
            import base64

            token = base64.b64encode(f"{name}:pw".encode()).decode()
            self.client.credentials(HTTP_AUTHORIZATION=f"Basic {token}")


REQUESTS = [
    ("get", "/{p}-{n}/", None),
    ("get", "/{p}-{n}/{pk}/", None),
    ("post", "/{p}-{n}/", {"name": "New"}),
    ("patch", "/{p}-{n}/{pk}/", {"name": "Renamed"}),
    ("delete", "/{p}-{n}/{pk}/", None),
    ("options", "/{p}-{n}/", None),
    ("head", "/{p}-{n}/", None),
]


@both_transports
class _ParityTests(Users):
    @urls
    async def test_the_same_decisions_as_drfs_classes(self):
        for name in PAIRS:
            for user in ("anonymous", "nobody", "inactive", "writer", "admin"):
                for method, template, data in REQUESTS:
                    with self.subTest(permission=name, user=user, method=method):
                        codes = []
                        for prefix in ("drf", "aiodrf"):
                            author = await Author.objects.acreate(
                                name=f"{prefix}-{user}"
                            )
                            url = template.format(p=prefix, n=name, pk=author.pk)
                            self.login(user)
                            response = await self.api(
                                method, url, data=data, format="json"
                            )
                            codes.append(response.status_code)
                        assert codes[0] == codes[1], codes

    @urls
    async def test_the_router_root_is_not_checked(self):
        self.login("nobody")
        response = await self.api("get", "/")
        assert response.status_code == 200


class CostTests(Users, TestCase):
    def setUp(self):
        super().setUp()
        self.client = AsyncAPIClient()

    @urls
    async def test_a_safe_request_needs_no_hop_for_the_check(self):
        self.login("nobody")
        with count_hops() as hops:
            response = await self.client.get("/aiodrf-model/")
        assert response.status_code == 200
        # BasicAuthentication reads the user from the database in a hop of its own.
        assert hops.calls == [
            "BasicAuthentication.authenticate",
            "ListModelMixin._list",
        ]

    @urls
    async def test_a_write_asks_the_backends_in_one_hop_with_drfs_queries(self):
        self.login("writer")
        with count_hops() as hops:
            response = await self.client.post(
                "/aiodrf-model/", {"name": "A"}, format="json"
            )
        assert response.status_code == 201, response.data
        assert hops.calls == [
            "BasicAuthentication.authenticate",
            "PermissionsMixin.has_perms",
            "CreateModelMixin._create",
        ]

    @urls
    async def test_an_async_queryset_hook_is_awaited(self):
        self.login("nobody")
        with count_hops() as hops:
            response = await self.client.get("/aqueryset/")
        assert response.status_code == 200
        assert hops.calls == [
            "BasicAuthentication.authenticate",
            "ListModelMixin._list",
        ]

    @urls
    def test_the_permission_queries_are_drfs(self):
        from rest_framework.test import APIClient

        client = APIClient()
        client.force_authenticate(self.users["writer"])
        counts = []
        for prefix in ("drf", "aiodrf"):
            with CaptureQueriesContext(connection) as queries:
                assert (
                    client.post(
                        f"/{prefix}-model/", {"name": "Q"}, format="json"
                    ).status_code
                    == 201
                )
            counts.append(len(queries))
        assert counts[0] == counts[1]


# -- Where the shortcut does not apply ------------------------------------------


class RequiredByCode(permissions.DjangoModelPermissions):
    def get_required_permissions(self, method, model_cls):
        return super().get_required_permissions(method, model_cls)


class Overridden(permissions.DjangoModelPermissions):
    def has_permission(self, request, view):
        return super().has_permission(request, view)


@pytest.mark.parametrize("permission", [RequiredByCode, Overridden])
@pytest.mark.django_db(transaction=True)
async def test_code_written_for_drf_runs_in_a_thread(permission):
    user = await User.objects.acreate(username="u")

    class View(viewsets.ModelViewSet):
        queryset = Author.objects.all()
        serializer_class = AuthorSerializer
        permission_classes = [permission]

    from aiodrf.test import AsyncAPIRequestFactory, force_authenticate

    request = AsyncAPIRequestFactory().get("/")
    force_authenticate(request, user)
    with count_hops() as hops:
        response = await View.as_view({"get": "list"})(request)
    assert response.status_code == 200
    assert len(hops.calls) == 2


class Refusing(permissions.DjangoModelPermissions):
    def has_permission(self, request, view):
        return False


async def test_a_direct_await_of_ahas_permission_asks_the_subclass():
    # The dispatcher resolves the override; a caller of the public async
    # method gets the subclass's decision too.
    from aiodrf.test import AsyncAPIRequestFactory

    request = AsyncAPIRequestFactory().get("/")
    # DRF's default grants: an authenticated user, a GET, no permission needed.
    request.user = User(username="u")
    view = type("View", (), {"queryset": Author.objects.all()})()
    assert await permissions.DjangoModelPermissions().ahas_permission(request, view)
    assert await Refusing().ahas_permission(request, view) is False


class Strict(AnonymousUser):
    # A user class with its own answer for "no permissions required".
    def has_perms(self, perm_list, obj=None):
        return False


@pytest.mark.django_db
async def test_a_user_class_with_its_own_has_perms_is_asked():
    from aiodrf.test import AsyncAPIRequestFactory

    class View(viewsets.ModelViewSet):
        queryset = Author.objects.all()
        serializer_class = AuthorSerializer
        permission_classes = [permissions.DjangoModelPermissionsOrAnonReadOnly]
        authentication_classes = []

    request = AsyncAPIRequestFactory().get("/")
    permission = permissions.DjangoModelPermissionsOrAnonReadOnly()
    view = View()
    view.request = request

    class Wrapped:
        user = Strict()
        method = "GET"

    assert await permission.ahas_permission(Wrapped(), view) is False
    assert (
        drf_permissions.DjangoModelPermissionsOrAnonReadOnly().has_permission(
            Wrapped(), view
        )
        is False
    )


def test_the_default_family_is_still_drfs():
    from aiodrf import permissions as default

    assert default.DjangoModelPermissions is drf_permissions.DjangoModelPermissions
    assert issubclass(
        permissions.DjangoModelPermissions, drf_permissions.DjangoModelPermissions
    )
