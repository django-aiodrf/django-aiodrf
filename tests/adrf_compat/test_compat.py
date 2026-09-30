"""
``aiodrf.contrib.adrf_compat``: an adrf project running unchanged on aiodrf
(``nox -s adrf_compat``, an environment without adrf).
"""

import importlib
import importlib.metadata
import inspect
import sys
import types

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from aiodrf import generics as aiodrf_generics
from aiodrf import views as aiodrf_views
from aiodrf.codemod import transform_source
from aiodrf.contrib import adrf_compat
from aiodrf.contrib.adrf_compat import AdrfCompatWarning
from aiodrf.response import Response
from aiodrf.settings import setting_error
from aiodrf.test import AsyncAPIClient, AsyncAPIRequestFactory, count_hops
from tests.adrf_compat import project
from tests.testapp.models import Author

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def clear_calls():
    project.calls.clear()


# -- The modules --------------------------------------------------------------------


def test_the_adrf_modules_are_aiodrf_shims():
    import adrf.generics
    import adrf.views

    assert adrf_compat.is_installed()
    assert sys.modules["adrf.views"] is importlib.import_module(
        "aiodrf.contrib.adrf_compat.adrf.views"
    )
    assert issubclass(adrf.views.APIView, aiodrf_views.APIView)
    assert issubclass(
        adrf.generics.ListCreateAPIView, aiodrf_generics.ListCreateAPIView
    )
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("adrf.not_a_module")


def test_an_adrf_module_warns_once_at_the_import():
    sys.modules.pop("adrf.utils", None)
    adrf_compat._warned.discard("adrf.utils")
    with pytest.warns(AdrfCompatWarning, match="adrf.utils is adrf's") as record:
        importlib.import_module("adrf.utils")
    assert [w.filename for w in record] == [__file__]


def test_a_submodule_another_shim_imported_is_still_adrfs_import():
    # The shim ``generics`` imports the shim ``mixins``; ``from adrf import
    # mixins`` must still go through the aliases, and warn.
    importlib.import_module("aiodrf.contrib.adrf_compat.adrf.generics")
    for name in ("adrf", "adrf.generics", "adrf.mixins"):
        sys.modules.pop(name, None)
    adrf_compat._warned.discard("adrf.mixins")
    import adrf.generics  # noqa: F401

    with pytest.warns(AdrfCompatWarning, match="adrf.mixins is adrf's"):
        from adrf import mixins
    assert sys.modules["adrf.mixins"] is mixins


def test_a_warning_turned_into_an_error_fails_every_import():
    import warnings

    sys.modules.pop("adrf.utils", None)
    adrf_compat._warned.discard("adrf.utils")
    with warnings.catch_warnings():
        warnings.simplefilter("error", AdrfCompatWarning)
        for _ in range(2):  # a retry does not get past the project's policy
            with pytest.raises(AdrfCompatWarning):
                importlib.import_module("adrf.utils")


def test_an_adrf_method_name_warns_once_where_the_class_is_defined():
    from adrf import viewsets

    adrf_compat._warned.discard("perform_adestroy")
    with pytest.warns(AdrfCompatWarning, match="perform_adestroy is adrf's") as record:

        class Destroying(viewsets.ModelViewSet):
            async def perform_adestroy(self, instance):
                await super().perform_adestroy(instance)

    assert [w.filename for w in record] == [__file__]
    assert Destroying.aperform_destroy is Destroying.__dict__["perform_adestroy"]


# -- An adrf project -----------------------------------------------------------------


async def test_list_with_adrf_hooks_and_fields():
    for name in ("Ursula", "Octavia", "Iain"):
        await Author.objects.acreate(name=name)
    response = await AsyncAPIClient().get("/authors/")
    assert response.status_code == 200
    assert response["X-Paginated"] == "adrf"
    assert response.data["count"] == 3
    assert [dict(row) for row in response.data["results"]] == [
        {"id": row["id"], "name": name, "shout": name.upper(), "initial": name[0]}
        for row, name in zip(
            response.data["results"], ["Ursula", "Octavia"], strict=True
        )
    ]
    # adrf's router: the action is adrf's name.
    assert project.calls == [
        ("check_async_permissions", ["NotBanned"]),
        ("alist", "alist"),
    ]


async def test_an_async_permission_denies_through_check_async_permissions():
    response = await AsyncAPIClient().get("/authors/", HTTP_X_BAN="1")
    assert response.status_code == 403
    assert response.data["detail"] == "banned"
    assert project.calls == [("check_async_permissions", ["NotBanned"])]


async def test_create_calls_perform_acreate():
    response = await AsyncAPIClient().post(
        "/authors/", {"name": "Ursula"}, format="json"
    )
    assert response.status_code == 201
    assert response.data["shout"] == "URSULA"
    assert "perform_acreate" in project.calls
    assert await Author.objects.filter(name="Ursula").aexists()


async def test_generic_update_calls_aupdate_for_put_and_patch():
    author = await Author.objects.acreate(name="Ursula")
    client = AsyncAPIClient()
    put = await client.put(f"/detail/{author.pk}/", {"name": "Octavia"}, format="json")
    patch = await client.patch(f"/detail/{author.pk}/", {"name": "Iain"}, format="json")
    assert (put.status_code, patch.status_code) == (200, 200)
    assert patch.data["shout"] == "IAIN"
    assert project.calls == [("aupdate", False), ("aupdate", True)]
    deleted = await client.delete(f"/detail/{author.pk}/")
    assert deleted.status_code == 204


async def test_request_api_view_shortcuts_and_adata():
    author = await Author.objects.acreate(name="Ursula")
    client = AsyncAPIClient()
    assert (await client.get("/echo/")).data == {"async_request": True}
    found = await client.get(f"/lookup/{author.pk}/")
    assert found.data == {
        "id": author.pk,
        "name": "Ursula",
        "shout": "URSULA",
        "initial": "U",
    }
    assert (await client.get("/lookup/not-a-number/")).status_code == 404
    assert (await client.get("/lookup/999999/")).status_code == 404


async def test_an_async_throttle():
    client = AsyncAPIClient()
    assert (await client.get("/throttled/")).status_code == 200
    throttled = await client.get("/throttled/", HTTP_X_THROTTLE="1")
    assert throttled.status_code == 429
    assert throttled["Retry-After"] == "7"


async def test_perform_create_with_drfs_name_is_called_unlike_in_adrf():
    from adrf import viewsets

    class Creating(project.AuthorViewSet):
        def perform_create(self, serializer):
            project.calls.append("perform_create")
            serializer.save()

    view = Creating.as_view({"post": "acreate"})
    request = AsyncAPIRequestFactory().post("/", {"name": "Ursula"}, format="json")
    response = await view(request)
    assert response.status_code == 201
    assert "perform_create" in project.calls
    assert issubclass(Creating, viewsets.ModelViewSet)


def test_the_router_routes_only_the_actions_a_viewset_has():
    from adrf import viewsets
    from adrf.routers import SimpleRouter

    class ReadOnly(viewsets.ReadOnlyModelViewSet):
        queryset = Author.objects.all()

    router = SimpleRouter()
    router.register("read-only", ReadOnly, basename="read-only")
    list_route, detail_route = router.urls
    assert list_route.callback.actions == {"get": "alist"}
    assert detail_route.callback.actions == {"get": "aretrieve"}


async def test_the_router_keeps_actions_a_viewset_defines_with_drfs_names():
    from adrf import viewsets
    from adrf.routers import SimpleRouter

    from tests.testapp.serializers import AuthorSerializer

    class OnlyList(viewsets.ViewSet):
        async def list(self, request):
            return Response(["own list"])

    class OverridesList(viewsets.ModelViewSet):
        queryset = Author.objects.all()
        serializer_class = AuthorSerializer

        async def list(self, request):
            return Response(["own list"])

    routes = {}
    for viewset in (OnlyList, OverridesList):
        router = SimpleRouter()
        router.register("x", viewset, basename=viewset.__name__)
        routes[viewset] = router.urls[0].callback
    assert routes[OnlyList].actions == {"get": "list"}
    # The class's own ``list`` is nearer than the inherited ``alist``.
    assert routes[OverridesList].actions == {"get": "list", "post": "acreate"}
    response = await routes[OverridesList](AsyncAPIRequestFactory().get("/x/"))
    assert response.data == ["own list"]


def test_the_concrete_generic_views_derive_from_adrfs_generic_view():
    from adrf import generics

    for name in generics.__all__:
        view = getattr(generics, name)
        if isinstance(view, type):
            assert issubclass(view, generics.GenericAPIView), name


def test_the_test_client_is_aiodrfs():
    from adrf.test import AsyncAPIClient as AdrfClient

    assert AdrfClient is AsyncAPIClient


# -- The same as aiodrf --------------------------------------------------------------


async def test_the_adrf_migration_fixture_behaves_and_costs_as_its_aiodrf_conversion():
    # tests/migration_adrf.py is written for adrf; test_migration_contract.py
    # runs it on adrf itself against the same conversion.
    from tests import migration_adrf

    converted = types.ModuleType("tests.migrated_adrf")
    code = transform_source(inspect.getsource(migration_adrf)).code
    exec(compile(code, "migrated_adrf.py", "exec"), vars(converted))  # noqa: S102 -- trusted fixture
    observations, costs = [], []
    for app in (migration_adrf, converted):
        await Author.objects.all().adelete()
        with override_settings(ROOT_URLCONF=app), count_hops() as hops:
            client = AsyncAPIClient()
            created = await client.post("/authors/", {"name": "Ursula"}, format="json")
            pk = created.data["id"]
            updated = await client.patch(
                f"/authors/{pk}/", {"name": "Octavia"}, format="json"
            )
            filtered = await client.get("/authors/?name=Octavia")
            action = await client.get("/authors/selected/")
            denied = await client.get("/authors/denied/")
            forbidden = await client.get("/authors/", HTTP_X_DENY="1")
            invalid = await client.post("/authors/", {"name": ""}, format="json")
        observations.append(
            (
                created.status_code,
                updated.status_code,
                updated.data["name"],
                [row["name"] for row in filtered.data],
                action.data,
                (denied.status_code, denied.data),
                forbidden.status_code,
                (invalid.status_code, invalid.data),
            )
        )
        costs.append(hops.count)
    assert observations[0] == observations[1]
    assert costs[0] == costs[1]


# -- Installing ----------------------------------------------------------------------


@pytest.fixture
def uninstalled():
    adrf_compat.uninstall()
    yield
    sys.modules.pop("adrf", None)
    adrf_compat.install()


def test_install_refuses_an_installed_adrf(uninstalled, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(importlib.metadata, "distribution", lambda name: object())
        with pytest.raises(ImproperlyConfigured, match="adrf is installed"):
            adrf_compat.install()
    assert not adrf_compat.is_installed()


def test_install_refuses_an_adrf_imported_before(uninstalled):
    sys.modules["adrf"] = types.ModuleType("adrf")
    with pytest.raises(ImproperlyConfigured, match="imported before"):
        adrf_compat.install()


def test_uninstall_removes_only_its_own_aliases(uninstalled):
    other = types.ModuleType("adrf")
    sys.modules["adrf"] = other
    adrf_compat.uninstall()
    assert sys.modules["adrf"] is other


def test_the_setting_is_a_boolean():
    assert setting_error("ADRF_COMPAT", True) is None
    assert "ADRF_COMPAT" in setting_error("ADRF_COMPAT", "yes")


async def test_legacy_permission_hooks_build_the_permissions_in_the_worker():
    from adrf.views import APIView
    from django.utils.asyncio import async_unsafe
    from rest_framework.permissions import IsAuthenticated

    calls = []

    class Legacy(APIView):
        authentication_classes = []

        @async_unsafe("get_permissions ran on the event loop")
        def get_permissions(self):
            return [IsAuthenticated()]

        def check_sync_permissions(self, request, permissions):
            calls.append([type(p).__name__ for p in permissions])
            return super().check_sync_permissions(request, permissions)

        async def get(self, request):
            return Response({})

    response = await Legacy.as_view()(AsyncAPIRequestFactory().get("/"))
    assert response.status_code == 403
    assert calls == [["IsAuthenticated"]]


@pytest.mark.parametrize("pure", [False, True])
async def test_check_async_throttles_calls_a_sync_wait_in_the_worker_unless_pure(pure):
    from adrf.views import APIView
    from django.utils.asyncio import async_unsafe
    from rest_framework.throttling import BaseThrottle

    from aiodrf.utils import async_safe

    def wait(self):
        return 7

    class Gate(BaseThrottle):
        async def allow_request(self, request, view):
            return False

    Gate.wait = (
        async_safe(wait) if pure else async_unsafe("wait ran on the event loop")(wait)
    )

    class Legacy(APIView):
        authentication_classes = []
        permission_classes = []
        throttle_classes = [Gate]

        async def check_async_throttles(self, request, throttles):
            return await super().check_async_throttles(request, throttles)

        async def get(self, request):
            return Response({})

    with count_hops() as hops:
        response = await Legacy.as_view()(AsyncAPIRequestFactory().get("/"))
    assert response.status_code == 429
    assert response["Retry-After"] == "7"
    assert [name for name in hops.calls if name.endswith(".wait")] == (
        [] if pure else [wait.__qualname__]
    )


# -- adrf's ModelSerializer writes ------------------------------------------------


class TitledAuthorSerializer(project.serializers.ModelSerializer):
    class Meta:
        model = Author
        fields = ["id", "name"]

    async def acreate(self, validated_data):
        validated_data["name"] = validated_data["name"].title()
        return await super().acreate(validated_data)

    async def aupdate(self, instance, validated_data):
        validated_data["name"] += "!"
        return await super().aupdate(instance, validated_data)


async def test_acreate_and_aupdate_extend_adrfs_model_serializer():
    # adrf's ModelSerializer implements both; overrides call them through super().
    serializer = TitledAuthorSerializer(data={"name": "ursula"})
    assert await serializer.ais_valid(), serializer.errors
    author = await serializer.asave()
    assert (await Author.objects.aget(pk=author.pk)).name == "Ursula"

    serializer = TitledAuthorSerializer(author, data={"name": "Le Guin"})
    assert await serializer.ais_valid(), serializer.errors
    await serializer.asave()
    assert (await Author.objects.aget(pk=author.pk)).name == "Le Guin!"


def test_adrfs_model_serializer_writes_are_not_an_override():
    # Without an override, the save is aiodrf's default (``ATOMIC_SAVE``).
    from aiodrf.utils import Impl, resolve_pair

    for names in (("create", "acreate"), ("update", "aupdate")):
        assert resolve_pair(project.AuthorSerializer, *names) is Impl.BASE
        assert resolve_pair(TitledAuthorSerializer, *names) is Impl.ASYNC
