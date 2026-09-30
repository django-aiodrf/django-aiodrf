"""
Every sync/async pair must be enforced from both sides.

DRF calls the synchronous member of a pair in many places aiodrf does not
control: a view's ``initial()`` override calling ``super()``, the browsable
API, ``OPTIONS`` metadata, schema generation, a ``perform_create`` written
for DRF. A policy or serializer that only implements the async member must
still take effect there, whatever class it derives from.
"""

from asgiref.sync import sync_to_async
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import path
from rest_framework import authentication as drf_authentication
from rest_framework import permissions as drf_permissions
from rest_framework import throttling as drf_throttling

from aiodrf import aio, serializers, viewsets
from aiodrf.response import Response
from aiodrf.test import count_hops
from aiodrf.utils import Impl, bridge_base, maybe_await, resolve_pair, run_sync
from aiodrf.views import APIView
from tests.base import both_transports
from tests.testapp.models import Author
from tests.testapp.serializers import AuthorSerializer

# Policies written against DRF's bases: no aiodrf base class provides a bridge.


class DuckDeny(drf_permissions.BasePermission):
    async def ahas_permission(self, request, view):
        return False


class AdrfStyleDeny(drf_permissions.BasePermission):
    async def has_permission(self, request, view):
        return False


class DuckDenyObject(drf_permissions.BasePermission):
    async def ahas_object_permission(self, request, view, obj):
        return False


class DuckThrottle(drf_throttling.BaseThrottle):
    async def aallow_request(self, request, view):
        return False

    def wait(self):
        return 7


class DuckAuthentication(drf_authentication.BaseAuthentication):
    async def aauthenticate(self, request):
        return (await User.objects.aget(username="alice"), "duck")


class LegacyInitial:
    # A common DRF idiom: extend ``initial()`` and call ``super()``. aiodrf
    # runs the whole synchronous ``initial`` in a thread.
    authentication_classes = []

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.initialised = True


def legacy_view(**attrs):
    class View(LegacyInitial, APIView):
        async def get(self, request):
            user = request.user
            return Response({"user": user.get_username() if user else None})

    return View.as_view(**attrs)


class LegacyObjectViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Author.objects.all()
    serializer_class = AuthorSerializer
    authentication_classes = []
    permission_classes = [DuckDenyObject]

    def get_object(self):
        # Written for DRF: DRF's ``get_object`` checks object permissions
        # synchronously.
        return super().get_object()


urlpatterns = [
    path("duck/", legacy_view(permission_classes=[DuckDeny])),
    path("adrf-style/", legacy_view(permission_classes=[AdrfStyleDeny])),
    path(
        "composed/",
        legacy_view(permission_classes=[drf_permissions.AllowAny & DuckDeny]),
    ),
    path(
        "throttled/",
        legacy_view(permission_classes=[], throttle_classes=[DuckThrottle]),
    ),
    path(
        "authenticated/",
        legacy_view(permission_classes=[], authentication_classes=[DuckAuthentication]),
    ),
    path("objects/<int:pk>/", LegacyObjectViewSet.as_view({"get": "retrieve"})),
]
urls = override_settings(ROOT_URLCONF=__name__)


@both_transports
class _SyncEnforcementTests:
    @classmethod
    def setUpTestData(cls):
        User.objects.create(username="alice")
        cls.author = Author.objects.create(name="Ursula")

    @urls
    async def test_async_only_permission(self):
        assert (await self.api("get", "/duck/")).status_code == 403

    @urls
    async def test_coroutine_under_the_sync_name(self):
        # An unawaited coroutine is truthy: it must never count as "allowed".
        assert (await self.api("get", "/adrf-style/")).status_code == 403

    @urls
    async def test_async_only_permission_inside_an_operator(self):
        assert (await self.api("get", "/composed/")).status_code == 403

    @urls
    async def test_async_only_object_permission(self):
        response = await self.api("get", f"/objects/{self.author.pk}/")
        assert response.status_code == 403

    @urls
    async def test_async_only_throttle(self):
        response = await self.api("get", "/throttled/")
        assert response.status_code == 429
        assert response["Retry-After"] == "7"

    @urls
    async def test_async_only_authentication(self):
        response = await self.api("get", "/authenticated/")
        assert response.data == {"user": "alice"}


# -- Serializers ---------------------------------------------------------------


class TracingSerializer(serializers.ModelSerializer):
    class Meta:
        model = Author
        fields = ["id", "name"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.trace = []

    async def ais_valid(self, *, raise_exception=False):
        self.trace.append("ais_valid")
        return await super().ais_valid(raise_exception=raise_exception)

    async def arun_validation(self, data=serializers.empty):
        self.trace.append("arun_validation")
        value = await super().arun_validation(data)
        return {**value, "name": value["name"].title()}

    async def asave(self, **kwargs):
        self.trace.append("asave")
        return await super().asave(**kwargs)

    async def ato_representation(self, instance):
        self.trace.append("ato_representation")
        return {**await super().ato_representation(instance), "bridged": True}


class SerializerBridgeTests(TestCase):
    async def test_sync_api_runs_each_async_override_once(self):
        def work():
            serializer = TracingSerializer(data={"name": "ursula le guin"})
            assert serializer.is_valid(), serializer.errors
            serializer.save()
            return serializer, serializer.data

        serializer, data = await sync_to_async(work)()
        assert data == {
            "id": serializer.instance.pk,
            "name": "Ursula Le Guin",
            "bridged": True,
        }
        assert serializer.trace == [
            "ais_valid",
            "arun_validation",
            "asave",
            "ato_representation",
        ]

    async def test_async_api_runs_each_async_override_once(self):
        serializer = TracingSerializer(data={"name": "ursula le guin"})
        assert await serializer.ais_valid()
        await serializer.asave()
        data = await serializer.adata()
        assert data["name"] == "Ursula Le Guin"
        assert data["bridged"] is True
        assert serializer.trace == [
            "ais_valid",
            "arun_validation",
            "asave",
            "ato_representation",
        ]


# -- View hooks ----------------------------------------------------------------


class AsyncOnlyHooksViewSet(viewsets.ModelViewSet):
    queryset = Author.objects.all()
    serializer_class = AuthorSerializer

    async def aget_object(self):
        return await Author.objects.aget(name="Ursula")

    async def aperform_create(self, serializer):
        # A plain DRF serializer: ``aio.save`` is its async API.
        await aio.save(serializer, name="CREATED")

    async def afilter_queryset(self, queryset):
        return queryset.filter(name="Ursula")


class ViewBridgeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.author = Author.objects.create(name="Ursula")
        Author.objects.create(name="Other")

    async def test_sync_callers_reach_async_only_view_hooks(self):
        # The browsable API and metadata classes call these synchronously.
        def work():
            view = AsyncOnlyHooksViewSet()
            view.kwargs = {}
            serializer = AuthorSerializer(data={"name": "x"})
            serializer.is_valid(raise_exception=True)
            view.perform_create(serializer)
            names = list(
                view.filter_queryset(view.get_queryset()).values_list("name", flat=True)
            )
            return view.get_object(), serializer.instance.name, names

        obj, created, names = await sync_to_async(work)()
        assert obj == self.author
        assert created == "CREATED"
        assert names == ["Ursula"]


# -- Pair resolution and instrumentation ----------------------------------------


@bridge_base
class PairBase:
    def work(self):
        return "base"

    async def awork(self):
        return "abase"


class AdrfStyle(PairBase):
    async def work(self):
        return "adrf"


class ExplicitAsync(AdrfStyle):
    async def awork(self):
        return "explicit"


def test_nearer_async_member_wins_over_a_coroutine_under_the_sync_name():
    assert resolve_pair(AdrfStyle, "work", "awork") is Impl.SYNC_IS_ASYNC
    assert resolve_pair(ExplicitAsync, "work", "awork") is Impl.ASYNC


async def test_hops_are_counted_when_they_happen():
    with count_hops() as hops:
        wrapper = run_sync(lambda: None)
        assert hops.count == 0
        await wrapper()
        await wrapper()
    assert hops.count == 2 == len(hops.calls)


async def test_responses_are_not_awaited_as_results():
    response = Response({"a": 1})
    assert await maybe_await(response) is response

    async def coroutine():
        return 1

    assert await maybe_await(coroutine()) == 1
