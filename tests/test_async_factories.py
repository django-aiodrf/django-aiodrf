"""Async factories preserve DRF's CRUD, precedence and challenge contracts."""

import asyncio

import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings
from django.urls import path
from django.utils.asyncio import async_unsafe
from rest_framework import authentication as drf_authentication
from rest_framework import serializers as drf_serializers
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView as DRFAPIView

from aiodrf.authentication import BaseAuthentication
from aiodrf.generics import GenericAPIView
from aiodrf.test import AsyncAPIRequestFactory
from aiodrf.views import APIView
from aiodrf.viewsets import ModelViewSet
from tests.base import both_transports
from tests.testapp.models import Tag


class TagSerializer(drf_serializers.ModelSerializer):
    class Meta:
        model = Tag
        fields = ["id", "name"]

    @async_unsafe("Serializer construction must run in a worker")
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


class TagViewSet(ModelViewSet):
    queryset = Tag.objects.all()
    serializer_class = TagSerializer
    authentication_classes = []
    permission_classes = []


class AsyncClassViewSet(TagViewSet):
    async def aget_serializer_class(self):
        asyncio.get_running_loop()
        return await super().aget_serializer_class()


class AsyncContextViewSet(TagViewSet):
    async def aget_serializer_context(self):
        context = await super().aget_serializer_context()
        context["tag_count"] = await Tag.objects.acount()
        return context


class AsyncFactoryViewSet(TagViewSet):
    async def aget_serializer(self, *args, **kwargs):
        asyncio.get_running_loop()
        return await super().aget_serializer(*args, **kwargs)


class AsyncOriginalNameViewSet(TagViewSet):
    async def get_serializer_context(self):
        return await self.aget_serializer_context()


VIEWSETS = {
    "class": AsyncClassViewSet,
    "context": AsyncContextViewSet,
    "factory": AsyncFactoryViewSet,
    "original": AsyncOriginalNameViewSet,
}
urlpatterns = [
    route
    for name, viewset in VIEWSETS.items()
    for route in (
        path(f"{name}/", viewset.as_view({"get": "list", "post": "create"})),
        path(
            f"{name}/<int:pk>/",
            viewset.as_view(
                {"get": "retrieve", "put": "update", "patch": "partial_update"}
            ),
        ),
    )
]


@both_transports
class _AsyncFactoryCRUD:
    @override_settings(ROOT_URLCONF=__name__)
    async def test_crud(self):
        for name in VIEWSETS:
            with self.subTest(factory=name):
                created = await self.api("post", f"/{name}/", data={"name": name})
                assert created.status_code == 201, created.data
                url = f"/{name}/{created.data['id']}/"
                assert (await self.api("get", url)).data == created.data
                assert (await self.api("get", f"/{name}/")).status_code == 200
                for method in ("put", "patch"):
                    updated = await self.api(
                        method, url, data={"name": f"{name}-{method}"}
                    )
                    assert updated.status_code == 200, updated.data
                    assert updated.data["name"] == f"{name}-{method}"


async def test_explicit_context_does_not_call_context_hook():
    class View(GenericAPIView):
        serializer_class = TagSerializer

        async def aget_serializer_context(self):
            raise AssertionError("Explicit context must be preserved")

    context = {"sentinel": object()}
    serializer = await View().aget_serializer(context=context)
    assert serializer.context is context


async def test_drf_context_is_built_without_a_thread_hop():
    # DRF's default is a dict of the request, format and view.
    from aiodrf.test import count_hops

    view = AsyncClassViewSet()
    view.request, view.format_kwarg = object(), None
    with count_hops() as hops:
        context = await view.aget_serializer_context()
    assert context == {"request": view.request, "format": None, "view": view}
    assert hops.calls == []


async def test_nearer_sync_and_instance_factory_overrides():
    class Parent(GenericAPIView):
        serializer_class = TagSerializer

        async def aget_serializer_context(self):
            return {"owner": "parent"}

    class Child(Parent):
        @async_unsafe("Sync context override ran on the loop")
        def get_serializer_context(self):
            return {"owner": "child"}

    view = Child()
    assert (await view.aget_serializer()).context == {"owner": "child"}

    async def context():
        return {"owner": "instance"}

    view.aget_serializer_context = context
    assert view._awaits_serializer()
    assert (await view.aget_serializer()).context == {"owner": "instance"}


async def test_sync_caller_bridges_async_factory_context():
    class View(GenericAPIView):
        serializer_class = TagSerializer

        async def aget_serializer_context(self):
            asyncio.get_running_loop()
            return {"async": True}

    serializer = await sync_to_async(View().get_serializer)()
    assert serializer.context == {"async": True}


async def test_dynamic_async_factory_checks_backend_allowlist():
    import msgspec

    class Schema(msgspec.Struct):
        name: str

    class View(GenericAPIView):
        async def aget_serializer_class(self):
            return Schema

    from django.core.exceptions import ImproperlyConfigured

    with (
        override_settings(AIODRF={"ALLOWED_SERIALIZER_BACKENDS": ["drf"]}),
        pytest.raises(ImproperlyConfigured, match="msgspec"),
    ):
        await View().aget_serializer(context={})


factory = AsyncAPIRequestFactory()


class RejectingAuthentication(BaseAuthentication):
    async def aauthenticate(self, request):
        raise AuthenticationFailed("invalid")

    async def aauthenticate_header(self, request):
        asyncio.get_running_loop()
        return 'Bearer realm="api"'


class Protected(APIView):
    authentication_classes = [RejectingAuthentication]
    permission_classes = []

    async def get(self, request):
        return Response({})


async def test_async_authentication_header_and_first_authenticator_only():
    class MustNotRun(RejectingAuthentication):
        async def aauthenticate_header(self, request):
            raise AssertionError("Only the first authenticator supplies the challenge")

    response = await Protected.as_view(
        authentication_classes=[RejectingAuthentication, MustNotRun]
    )(factory.get("/"))
    assert response.status_code == 401
    assert response["WWW-Authenticate"] == 'Bearer realm="api"'


@pytest.mark.parametrize("header", [None, "Custom"])
async def test_async_view_header_override(header):
    class View(Protected):
        async def aget_authenticate_header(self, request):
            return header

    response = await View.as_view()(factory.get("/"))
    assert response.status_code == (401 if header else 403)
    assert response.get("WWW-Authenticate") == header


async def test_sync_header_override_is_offloaded():
    class Auth(drf_authentication.BaseAuthentication):
        def authenticate(self, request):
            raise AuthenticationFailed()

        @async_unsafe("Sync header ran on the loop")
        def authenticate_header(self, request):
            return "Custom"

    response = await Protected.as_view(authentication_classes=[Auth])(factory.get("/"))
    assert response.status_code == 401
    assert response["WWW-Authenticate"] == "Custom"


async def test_sync_drf_view_bridges_async_authentication_header():
    class View(DRFAPIView):
        authentication_classes = [RejectingAuthentication]
        permission_classes = []

        def get(self, request):
            return Response({})

    response = await sync_to_async(View.as_view())(factory.get("/"))
    assert response.status_code == 401
    assert response["WWW-Authenticate"] == 'Bearer realm="api"'


async def test_header_cancellation_propagates():
    class View(Protected):
        async def aget_authenticate_header(self, request):
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await View.as_view()(factory.get("/"))


async def test_non_authentication_error_does_not_call_header_hook():
    class View(Protected):
        authentication_classes = []

        async def get(self, request):
            raise PermissionDenied()

        async def aget_authenticate_header(self, request):
            raise AssertionError("Not an authentication error")

    assert (await View.as_view()(factory.get("/"))).status_code == 403


async def test_cancelling_serializer_factory_does_not_reach_save():
    entered = asyncio.Event()
    saved = []

    class View(TagViewSet):
        async def aget_serializer(self, *args, **kwargs):
            entered.set()
            await asyncio.Event().wait()
            return await super().aget_serializer(*args, **kwargs)

        async def aperform_create(self, serializer):
            saved.append(serializer)

    task = asyncio.create_task(
        View.as_view({"post": "create"})(
            factory.post("/", {"name": "cancelled"}, format="json")
        )
    )
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert saved == []


async def test_awaitable_header_with_a_sync_prefix_runs_in_worker():
    class Auth(RejectingAuthentication):
        @async_unsafe("Header wrapper ran on the loop")
        def aauthenticate_header(self, request):
            return super().aauthenticate_header(request)

    response = await Protected.as_view(authentication_classes=[Auth])(factory.get("/"))
    assert response.status_code == 401
    assert response["WWW-Authenticate"] == 'Bearer realm="api"'
