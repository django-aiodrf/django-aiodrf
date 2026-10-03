"""
A project that keeps DRF and adds aiodrf next to it (docs/guides/drf-integration.md).

DRF views and aiodrf views share one URLconf, one router, one
``REST_FRAMEWORK``, the same serializers and the same policy classes. Every
case runs through Django's async and sync test handlers, not a socket server.
"""

import asyncio
import inspect
import json
from io import StringIO
from types import ModuleType
from unittest import mock

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.db import connections, transaction
from django.test import override_settings
from django.urls import include, path
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.views import SpectacularAPIView
from rest_framework import generics as drf_generics
from rest_framework import permissions as drf_permissions
from rest_framework import serializers as drf_serializers
from rest_framework import views as drf_views
from rest_framework import viewsets as drf_viewsets
from rest_framework.decorators import api_view as drf_api_view
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response as DRFResponse
from rest_framework.routers import DefaultRouter
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import exception_handler as drf_exception_handler

from aiodrf import (
    aio,
    authentication,
    generics,
    permissions,
    serializers,
    throttling,
    viewsets,
)
from aiodrf.decorators import api_view
from aiodrf.response import Response
from aiodrf.test import (
    APIClient,
    APIRequestFactory,
    AsyncAPIClient,
    AsyncAPIRequestFactory,
    count_hops,
    force_authenticate,
)
from aiodrf.views import APIView
from tests.base import both_transports
from tests.testapp.models import Author, Book
from tests.testapp.serializers import AuthorSerializer

# -- Shared code: serializers, policies, a mixin -------------------------------------


class AsyncAuthorSerializer(serializers.ModelSerializer):
    """An aiodrf serializer with async members, also used by a DRF view."""

    books = serializers.SerializerMethodField()

    class Meta:
        model = Author
        fields = ["id", "name", "books"]

    async def validate_name(self, value):
        if await Author.objects.filter(name__iexact=value).aexists():
            raise serializers.ValidationError("taken")
        return value

    async def acreate(self, validated_data):
        return await Author.objects.acreate(name=validated_data["name"].upper())

    async def get_books(self, obj) -> int:
        return await obj.books.acount()


class DRFAcreateSerializer(drf_serializers.ModelSerializer):
    """A plain DRF serializer whose creation must be awaited."""

    class Meta:
        model = Author
        fields = ["id", "name"]

    async def acreate(self, validated_data):
        return await Author.objects.acreate(name=validated_data["name"].upper())


class AiodrfAcreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Author
        fields = ["id", "name"]

    async def acreate(self, validated_data):
        return await Author.objects.acreate(name=validated_data["name"].upper())


class OwnedBookSerializer(drf_serializers.ModelSerializer):
    class Meta:
        model = Book
        fields = ["id", "title", "isbn", "author", "owner"]
        read_only_fields = ["owner"]


class PairPermission(drf_permissions.BasePermission):
    """Both members: DRF calls the first, aiodrf awaits the second."""

    calls = []

    def has_permission(self, request, view):
        self.calls.append("has_permission")
        return "HTTP_X_DENY" not in request.META

    async def ahas_permission(self, request, view):
        self.calls.append("ahas_permission")
        return "HTTP_X_DENY" not in request.META


class AsyncOnlyOnDRFBase(drf_permissions.BasePermission):
    async def ahas_permission(self, request, view):
        return False


class AsyncOnlyOnAiodrfBase(permissions.BasePermission):
    async def ahas_permission(self, request, view):
        return False


class AsyncOnlyAuthentication(authentication.BaseAuthentication):
    async def aauthenticate(self, request):
        return (await User.objects.aget(username="alice"), None)


class AsyncOnlyThrottle(throttling.BaseThrottle):
    async def aallow_request(self, request, view):
        return False

    def wait(self):
        return 7


class SyncOnlyPermission(drf_permissions.BasePermission):
    def has_permission(self, request, view):
        return "HTTP_X_DENY" not in request.META


class OwnedMixin:
    """Written for DRF; used unchanged by a DRF and an aiodrf viewset."""

    serializer_class = OwnedBookSerializer
    permission_classes = [drf_permissions.IsAuthenticated]

    def get_queryset(self):
        return Book.objects.filter(owner=self.request.user)

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)


class AsyncOnlyQuerysetMixin:
    queryset = Author.objects.all()
    serializer_class = AuthorSerializer

    async def aget_queryset(self):
        return Author.objects.filter(name="nobody")


def shared_exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is not None:
        response.data["handler"] = "shared"
    return response


class FailingCreateSerializer(drf_serializers.ModelSerializer):
    class Meta:
        model = Author
        fields = ["id", "name"]

    def create(self, validated_data):
        super().create(validated_data)
        raise RuntimeError("after the insert")


class AiodrfFailingCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Author
        fields = ["id", "name"]

    def create(self, validated_data):
        super().create(validated_data)
        raise RuntimeError("after the insert")


# -- Views of both kinds ----------------------------------------------------------------


class DRFAuthorViewSet(drf_viewsets.ModelViewSet):
    # Ordered: the tests compare the listed names, and PostgreSQL returns an
    # unordered query in any order.
    queryset = Author.objects.order_by("pk")
    serializer_class = AuthorSerializer


class AioAuthorViewSet(viewsets.ModelViewSet):
    queryset = Author.objects.order_by("pk")
    serializer_class = AuthorSerializer


class NonAtomicAuthorViewSet(viewsets.ModelViewSet):
    queryset = Author.objects.all()
    serializer_class = AuthorSerializer

    # Copied to the view function by ``as_view()``, which Django's handler reads.
    @transaction.non_atomic_requests
    async def dispatch(self, request, *args, **kwargs):
        return await super().dispatch(request, *args, **kwargs)


class DRFOwnedBooks(OwnedMixin, drf_viewsets.ModelViewSet):
    pass


class AioOwnedBooks(OwnedMixin, viewsets.ModelViewSet):
    pass


class DRFAsyncOnlyQueryset(AsyncOnlyQuerysetMixin, drf_viewsets.ReadOnlyModelViewSet):
    pass


class AioAsyncOnlyQueryset(AsyncOnlyQuerysetMixin, viewsets.ReadOnlyModelViewSet):
    pass


router = DefaultRouter()
router.register("drf-authors", DRFAuthorViewSet, basename="drf-author")
router.register("aio-authors", AioAuthorViewSet, basename="aio-author")
router.register(
    "non-atomic-authors", NonAtomicAuthorViewSet, basename="non-atomic-author"
)
router.register("drf-owned", DRFOwnedBooks, basename="drf-owned")
router.register("aio-owned", AioOwnedBooks, basename="aio-owned")
router.register(
    "drf-async-queryset", DRFAsyncOnlyQueryset, basename="drf-async-queryset"
)
router.register(
    "aio-async-queryset", AioAsyncOnlyQueryset, basename="aio-async-queryset"
)


def _loop_running():
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


class DRFMe(drf_views.APIView):
    def get(self, request):
        return DRFResponse(
            {"user": request.user.get_username(), "loop": _loop_running()}
        )


class AioMe(APIView):
    async def get(self, request):
        # ``request.user`` is loaded before an async handler runs.
        return Response({"user": request.user.get_username(), "loop": _loop_running()})


class DRFPolicies(drf_views.APIView):
    def get(self, request):
        return DRFResponse({"ok": True})


class AioPolicies(APIView):
    async def get(self, request):
        return Response({"ok": True})


class DRFMissing(drf_views.APIView):
    def get(self, request):
        raise NotFound


class AioMissing(APIView):
    async def get(self, request):
        raise NotFound


@drf_api_view(["GET"])
def drf_function(request):
    return DRFResponse({"loop": _loop_running()})


@api_view(["GET"])
async def aio_function(request):
    return Response({"loop": _loop_running()})


@api_view(["GET"])
def aio_sync_function(request):
    return Response({"loop": _loop_running()})


class DRFAsyncSerializerView(drf_generics.ListCreateAPIView):
    """A DRF view driving an aiodrf serializer through its sync bridges."""

    queryset = Author.objects.order_by("pk")
    serializer_class = AsyncAuthorSerializer


class DRFBridgeCall(drf_views.APIView):
    """A DRF view calling ``aiodrf.aio`` through ``async_to_sync``."""

    def post(self, request):
        serializer = AuthorSerializer(data=request.data)
        async_to_sync(aio.is_valid)(serializer, raise_exception=True)
        return DRFResponse(serializer.validated_data)


class AioCreate(generics.CreateAPIView):
    queryset = Author.objects.all()
    serializer_class = DRFAcreateSerializer


class AioSyncPerformCreate(AioCreate):
    def perform_create(self, serializer):
        serializer.save()


class DRFCreate(drf_generics.CreateAPIView):
    queryset = Author.objects.all()
    serializer_class = DRFAcreateSerializer


class DRFWriteThenReject(drf_views.APIView):
    def post(self, request):
        Author.objects.create(name=request.data["name"])
        raise ValidationError("rejected")


class AioWriteThenReject(APIView):
    def post(self, request):
        Author.objects.create(name=request.data["name"])
        raise ValidationError("rejected")


class DRFFailingCreate(drf_generics.CreateAPIView):
    queryset = Author.objects.all()
    serializer_class = FailingCreateSerializer


class DRFAsyncHandler(drf_views.APIView):
    async def get(self, request):
        return DRFResponse({})

    def finalize_response(self, request, response, *args, **kwargs):
        # Test scaffolding: close the coroutine DRF got instead of a
        # response, so that it is not reported later as never awaited.
        if inspect.iscoroutine(response):
            response.close()
        return super().finalize_response(request, response, *args, **kwargs)


def policies(view, **kwargs):
    return view.as_view(authentication_classes=[], **kwargs)


urlpatterns = [
    path("", include(router.urls)),
    path("drf/me/", DRFMe.as_view()),
    path("aio/me/", AioMe.as_view()),
    path("drf/pair/", policies(DRFPolicies, permission_classes=[PairPermission])),
    path("aio/pair/", policies(AioPolicies, permission_classes=[PairPermission])),
    path(
        "drf/async-only/",
        policies(DRFPolicies, permission_classes=[AsyncOnlyOnDRFBase]),
    ),
    path(
        "aio/async-only/",
        policies(AioPolicies, permission_classes=[AsyncOnlyOnDRFBase]),
    ),
    path(
        "drf/bridged/",
        policies(DRFPolicies, permission_classes=[AsyncOnlyOnAiodrfBase]),
    ),
    path(
        "aio/bridged/",
        policies(AioPolicies, permission_classes=[AsyncOnlyOnAiodrfBase]),
    ),
    path(
        "drf/async-authentication/",
        DRFMe.as_view(authentication_classes=[AsyncOnlyAuthentication]),
    ),
    path(
        "aio/async-authentication/",
        AioMe.as_view(authentication_classes=[AsyncOnlyAuthentication]),
    ),
    path(
        "drf/async-throttle/",
        policies(DRFPolicies, throttle_classes=[AsyncOnlyThrottle]),
    ),
    path(
        "aio/async-throttle/",
        policies(AioPolicies, throttle_classes=[AsyncOnlyThrottle]),
    ),
    path(
        "drf/sync-only/", policies(DRFPolicies, permission_classes=[SyncOnlyPermission])
    ),
    path(
        "aio/sync-only/", policies(AioPolicies, permission_classes=[SyncOnlyPermission])
    ),
    path("drf/throttled/", policies(DRFPolicies, throttle_classes=[AnonRateThrottle])),
    path("aio/throttled/", policies(AioPolicies, throttle_classes=[AnonRateThrottle])),
    path("drf/missing/", DRFMissing.as_view()),
    path("aio/missing/", AioMissing.as_view()),
    path("drf/function/", drf_function),
    path("aio/function/", aio_function),
    path("aio/sync-function/", aio_sync_function),
    path("drf/async-serializer/", DRFAsyncSerializerView.as_view()),
    path("drf/bridge-call/", DRFBridgeCall.as_view()),
    path("aio/create/", AioCreate.as_view()),
    path("aio/sync-perform-create/", AioSyncPerformCreate.as_view()),
    path("drf/create/", DRFCreate.as_view()),
    path("drf/write-then-reject/", DRFWriteThenReject.as_view()),
    path(
        "aio/write-then-reject/",
        transaction.non_atomic_requests(AioWriteThenReject.as_view()),
    ),
    path("aio/write-then-reject-atomic/", AioWriteThenReject.as_view()),
    path("drf/failing-create/", DRFFailingCreate.as_view()),
    path(
        "drf/aiodrf-failing-create/",
        DRFFailingCreate.as_view(serializer_class=AiodrfFailingCreateSerializer),
    ),
    path("drf/async-handler/", DRFAsyncHandler.as_view()),
    path("schema/", SpectacularAPIView.as_view()),
]
urls = override_settings(ROOT_URLCONF=__name__)


# -- Tests ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "attr",
    [
        "authentication_classes",
        "permission_classes",
        "renderer_classes",
        "parser_classes",
        "throttle_classes",
        "pagination_class",
        "filter_backends",
        "content_negotiation_class",
        "metadata_class",
        "versioning_class",
        "settings",
    ],
)
def test_both_kinds_read_the_same_rest_framework_defaults(attr):
    assert getattr(viewsets.ModelViewSet, attr) == getattr(
        drf_viewsets.ModelViewSet, attr
    )
    if hasattr(drf_views.APIView, attr):
        assert getattr(APIView, attr) == getattr(drf_views.APIView, attr)


@pytest.mark.django_db
def test_request_factories_call_each_kind_as_it_is():
    user = User.objects.create(username="alice")
    request = APIRequestFactory().get("/")
    force_authenticate(request, user)
    assert DRFMe.as_view()(request).data["user"] == "alice"
    # An aiodrf view is a coroutine function: synchronous code calls it
    # through ``async_to_sync``, as Django's WSGI handler does.
    view = AioMe.as_view()
    assert inspect.iscoroutinefunction(view)
    request = APIRequestFactory().get("/")
    force_authenticate(request, user)
    assert async_to_sync(view)(request).data["user"] == "alice"


async def test_an_async_request_factory_for_aiodrf_views(worker_connections):
    user = await User.objects.acreate(username="alice")
    request = AsyncAPIRequestFactory().get("/")
    force_authenticate(request, user)
    assert (await AioMe.as_view()(request)).data["user"] == "alice"


@both_transports
class _MixedProjectTests:
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create(username="alice")
        cls.bob = User.objects.create(username="bob")
        cls.author = Author.objects.create(name="Ursula")

    def setUp(self):
        super().setUp()
        cache.clear()
        PairPermission.calls.clear()

    async def login(self, user):
        if self.transport == "asgi":
            await self.client.aforce_login(user)
        else:
            await sync_to_async(self.client.force_login)(user)

    # One router, one URLconf

    @urls
    async def test_one_router_serves_both_kinds(self):
        root = await self.api("get", "/")
        assert {"drf-authors", "aio-authors"} <= set(root.data)
        for prefix in ("drf", "aio"):
            created = await self.api(
                "post", f"/{prefix}-authors/", data={"name": prefix}
            )
            assert created.status_code == 201, created.data
        for prefix in ("drf", "aio"):
            listed = await self.api("get", f"/{prefix}-authors/")
            assert [author["name"] for author in listed.data] == [
                "Ursula",
                "drf",
                "aio",
            ]

    @urls
    async def test_an_aiodrf_viewset_costs_one_hop_under_both_transports(self):
        with count_hops() as hops:
            assert (await self.api("get", "/aio-authors/")).status_code == 200
        assert hops.calls == ["ListModelMixin._list"]
        with count_hops() as hops:
            assert (await self.api("get", "/drf-authors/")).status_code == 200
        assert hops.calls == []

    @urls
    async def test_function_views_of_both_decorators(self):
        # DRF's views never see an event loop; an async aiodrf view always
        # runs on one, under WSGI as well; a sync aiodrf function runs in a
        # thread.
        assert (await self.api("get", "/drf/function/")).data == {"loop": False}
        assert (await self.api("get", "/aio/function/")).data == {"loop": True}
        assert (await self.api("get", "/aio/sync-function/")).data == {"loop": False}

    # Shared REST_FRAMEWORK settings and policies

    @urls
    async def test_session_authentication_is_shared(self):
        await self.login(self.alice)
        for prefix in ("drf", "aio"):
            response = await self.api("get", f"/{prefix}/me/")
            assert response.data["user"] == "alice"

    @urls
    async def test_force_authenticate_reaches_both_kinds(self):
        self.client.force_authenticate(self.bob)
        for prefix in ("drf", "aio"):
            assert (await self.api("get", f"/{prefix}/me/")).data["user"] == "bob"

    @urls
    async def test_the_exception_handler_setting_is_shared(self):
        handler = f"{__name__}.shared_exception_handler"
        with override_settings(
            REST_FRAMEWORK={**settings.REST_FRAMEWORK, "EXCEPTION_HANDLER": handler}
        ):
            for prefix in ("drf", "aio"):
                response = await self.api("get", f"/{prefix}/missing/")
                assert response.status_code == 404
                assert response.data["handler"] == "shared"

    @urls
    async def test_throttle_history_is_shared(self):
        # ``anon: 3/min`` in the test settings: one cache key for both kinds.
        statuses = [
            (await self.api("get", path)).status_code
            for path in ("/drf/throttled/", "/aio/throttled/", "/drf/throttled/")
        ]
        assert statuses == [200, 200, 200]
        assert (await self.api("get", "/aio/throttled/")).status_code == 429
        assert (await self.api("get", "/drf/throttled/")).status_code == 429

    @urls
    async def test_a_permission_with_both_members(self):
        assert (await self.api("get", "/drf/pair/")).status_code == 200
        assert (await self.api("get", "/aio/pair/")).status_code == 200
        assert PairPermission.calls == ["has_permission", "ahas_permission"]
        assert (await self.api("get", "/drf/pair/", HTTP_X_DENY="1")).status_code == 403
        assert (await self.api("get", "/aio/pair/", HTTP_X_DENY="1")).status_code == 403

    @urls
    async def test_an_async_only_permission_on_drfs_base_allows_in_drf_views(self):
        # DRF calls BasePermission.has_permission, which returns True.
        assert (await self.api("get", "/drf/async-only/")).status_code == 200
        assert (await self.api("get", "/aio/async-only/")).status_code == 403

    @urls
    async def test_an_async_only_permission_on_aiodrfs_base_denies_in_both(self):
        assert (await self.api("get", "/drf/bridged/")).status_code == 403
        assert (await self.api("get", "/aio/bridged/")).status_code == 403

    @urls
    async def test_async_only_authentication_and_throttles_on_aiodrfs_bases(self):
        for prefix in ("drf", "aio"):
            response = await self.api("get", f"/{prefix}/async-authentication/")
            assert response.data["user"] == "alice"
            response = await self.api("get", f"/{prefix}/async-throttle/")
            assert response.status_code == 429
            assert response["Retry-After"] == "7"

    @urls
    async def test_a_sync_permission_costs_a_hop_in_aiodrf_only(self):
        with count_hops() as hops:
            assert (await self.api("get", "/drf/sync-only/")).status_code == 200
        assert hops.calls == []
        with count_hops() as hops:
            assert (await self.api("get", "/aio/sync-only/")).status_code == 200
        assert hops.count == 1, hops.calls
        response = await self.api("get", "/aio/sync-only/", HTTP_X_DENY="1")
        assert response.status_code == 403

    # Shared code

    @urls
    async def test_a_mixin_written_for_drf_serves_both(self):
        await self.login(self.alice)
        for prefix, isbn in (("drf", "1"), ("aio", "2")):
            response = await self.api(
                "post",
                f"/{prefix}-owned/",
                data={"title": prefix, "isbn": isbn, "author": self.author.pk},
            )
            assert response.status_code == 201, response.data
            assert response.data["owner"] == self.alice.pk
        await Book.objects.acreate(
            title="bob's", isbn="3", author=self.author, owner=self.bob
        )
        for prefix in ("drf", "aio"):
            listed = await self.api("get", f"/{prefix}-owned/")
            assert [book["title"] for book in listed.data] == ["drf", "aio"]

    @urls
    async def test_the_mixin_costs_aiodrf_one_hop(self):
        await self.login(self.alice)
        with count_hops() as hops:
            response = await self.api(
                "post",
                "/aio-owned/",
                data={"title": "t", "isbn": "9", "author": self.author.pk},
            )
        assert response.status_code == 201
        assert hops.calls == ["CreateModelMixin._create"]

    @urls
    async def test_drf_views_ignore_async_hooks(self):
        # DRF calls ``get_queryset``; ``aget_queryset`` is aiodrf's.
        assert len((await self.api("get", "/drf-async-queryset/")).data) == 1
        assert (await self.api("get", "/aio-async-queryset/")).data == []

    # Serializers across kinds

    @urls
    async def test_an_aiodrf_serializer_in_a_drf_view(self):
        created = await self.api("post", "/drf/async-serializer/", data={"name": "ada"})
        assert created.status_code == 201, created.data
        assert created.data == {"id": created.data["id"], "name": "ADA", "books": 0}
        taken = await self.api(
            "post", "/drf/async-serializer/", data={"name": "ursula"}
        )
        assert taken.status_code == 400
        assert taken.data == {"name": ["taken"]}
        listed = await self.api("get", "/drf/async-serializer/")
        assert [author["name"] for author in listed.data] == ["Ursula", "ADA"]

    @urls
    async def test_a_drf_view_can_call_aio_through_async_to_sync(self):
        response = await self.api("post", "/drf/bridge-call/", data={"name": "ada"})
        assert response.data == {"name": "ada"}

    @urls
    async def test_async_creation_of_a_plain_drf_serializer(self):
        # aiodrf's generic view awaits ``acreate``.
        response = await self.api("post", "/aio/create/", data={"name": "ada"})
        assert response.data["name"] == "ADA"
        assert response.status_code == 201
        # DRF's view never calls it: DRF's default ``create`` saves as sent.
        response = await self.api("post", "/drf/create/", data={"name": "bob"})
        assert response.status_code == 201
        assert response.data["name"] == "bob"
        # A ``perform_create`` written for DRF in an aiodrf view refuses it.
        with pytest.raises(ImproperlyConfigured, match="perform_create"):
            await self.api("post", "/aio/sync-perform-create/", data={"name": "cy"})

    # Browsable API and schema

    @urls
    async def test_browsable_api_for_both_kinds(self):
        for url in ("/drf-authors/", "/aio-authors/", "/drf/async-serializer/"):
            response = await self.api("get", url, HTTP_ACCEPT="text/html")
            assert response.status_code == 200
            assert response["Content-Type"].startswith("text/html")
            assert b"<form" in response.content

    @urls
    async def test_the_schema_view_documents_both_kinds(self):
        response = await self.api("get", "/schema/", HTTP_ACCEPT="application/json")
        assert response.status_code == 200
        paths = json.loads(response.content)["paths"]
        assert {"/drf-authors/", "/aio-authors/", "/drf/me/", "/aio/me/"} <= set(paths)

    @urls
    async def test_atomic_save_follows_aiodrf_serializers_into_drf_views(self):
        # A plain DRF serializer in a DRF view: DRF's autocommit, the
        # insert stays. An aiodrf serializer's save() runs in
        # ``transaction.atomic()`` (ATOMIC_SAVE), whichever view calls it.
        with pytest.raises(RuntimeError, match="after the insert"):
            await self.api("post", "/drf/failing-create/", data={"name": "drf"})
        with pytest.raises(RuntimeError, match="after the insert"):
            await self.api("post", "/drf/aiodrf-failing-create/", data={"name": "aio"})
        names = [author.name async for author in Author.objects.order_by("pk")]
        assert names == ["Ursula", "drf"]
        with (
            override_settings(AIODRF={"ATOMIC_SAVE": False}, FASTDRF={}),
            pytest.raises(RuntimeError, match="after the insert"),
        ):
            await self.api("post", "/drf/aiodrf-failing-create/", data={"name": "off"})
        assert await Author.objects.filter(name="off").aexists()

    @urls
    async def test_the_serializer_backend_applies_to_aiodrf_views_only(self):
        from fastdrf import compiler

        with (
            override_settings(FASTDRF={"SERIALIZER_BACKEND": "msgspec"}, AIODRF={}),
            mock.patch.object(
                compiler, "compiled_for", wraps=compiler.compiled_for
            ) as compiled,
        ):
            drf = await self.api("get", "/drf-authors/")
            assert compiled.call_count == 0
            aiodrf = await self.api("get", "/aio-authors/")
            assert compiled.call_count == 1
        assert drf.content == aiodrf.content

    @urls
    async def test_an_async_handler_in_a_drf_view_fails(self):
        # DRF calls the handler and gets a coroutine it does not await.
        with pytest.raises(AssertionError, match="received a `<class 'coroutine'>`"):
            await self.api("get", "/drf/async-handler/")


# -- ATOMIC_REQUESTS: a request transaction for DRF views only ---------------------------

TRANSPORTS = ["asgi", "wsgi"]


async def request(transport, method, path, **kwargs):
    if transport == "asgi":
        return await getattr(AsyncAPIClient(), method)(path, **kwargs)
    return await sync_to_async(getattr(APIClient(), method))(path, **kwargs)


atomic_requests = mock.patch.dict(
    connections.settings["default"], {"ATOMIC_REQUESTS": True}
)


@urls
@atomic_requests
@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_atomic_requests_roll_back_drf_views_only(transport, worker_connections):
    # DRF's exception handler marks the request's transaction for rollback.
    response = await request(
        transport, "post", "/drf/write-then-reject/", data={"name": "drf"}
    )
    assert response.status_code == 400
    # The aiodrf view is excluded with ``non_atomic_requests``: its write stays.
    response = await request(
        transport, "post", "/aio/write-then-reject/", data={"name": "aio"}
    )
    assert response.status_code == 400
    assert [author.name async for author in Author.objects.all()] == ["aio"]


@urls
@atomic_requests
@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_atomic_requests_refuse_async_views(transport, worker_connections):
    with pytest.raises(RuntimeError, match="ATOMIC_REQUESTS with async views"):
        await request(
            transport, "post", "/aio/write-then-reject-atomic/", data={"name": "x"}
        )
    assert not await Author.objects.aexists()


@urls
@atomic_requests
@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_a_routed_viewset_opts_out_through_its_dispatch(
    transport, worker_connections
):
    response = await request(
        transport, "post", "/non-atomic-authors/", data={"name": "ada"}
    )
    assert response.status_code == 201
    assert (await request(transport, "get", "/non-atomic-authors/")).status_code == 200


# -- drf-spectacular across both kinds -----------------------------------------------------


def _renamed(schema, prefix):
    return json.loads(
        json.dumps(schema)
        .replace(f"{prefix}-author", "X")
        .replace(f"{prefix}_author", "X")
    )


def test_the_same_serializer_documents_the_same_way_in_both_kinds():
    schema = SchemaGenerator(patterns=router.urls).get_schema(request=None, public=True)
    paths = schema["paths"]
    for suffix in ("/", "/{id}/"):
        drf_operations = _renamed(paths[f"/drf-authors{suffix}"], "drf")
        aiodrf_operations = _renamed(paths[f"/aio-authors{suffix}"], "aio")
        assert drf_operations == aiodrf_operations


def test_the_spectacular_command_validates_a_mixed_urlconf():
    authors = DefaultRouter()
    authors.register("drf-authors", DRFAuthorViewSet, basename="drf-author")
    authors.register("aio-authors", AioAuthorViewSet, basename="aio-author")
    urlconf = ModuleType("with_drf_schema_urls")
    urlconf.urlpatterns = [
        path("", include(authors.urls)),
        # An aiodrf serializer with async members in a DRF view, a plain
        # DRF serializer in an aiodrf view.
        path("drf/async-serializer/", DRFAsyncSerializerView.as_view()),
        path("aio/create/", AioCreate.as_view()),
    ]
    out = StringIO()
    with override_settings(ROOT_URLCONF=urlconf):
        call_command(
            "spectacular",
            "--format",
            "openapi-json",
            "--validate",
            "--fail-on-warn",
            stdout=out,
        )
    paths = json.loads(out.getvalue())["paths"]
    books = paths["/drf/async-serializer/"]["get"]["responses"]["200"]
    assert books["content"]["application/json"]["schema"]["items"]["$ref"].endswith(
        "AsyncAuthor"
    )
    assert set(paths) == {
        "/drf-authors/",
        "/drf-authors/{id}/",
        "/aio-authors/",
        "/aio-authors/{id}/",
        "/drf/async-serializer/",
        "/aio/create/",
    }
