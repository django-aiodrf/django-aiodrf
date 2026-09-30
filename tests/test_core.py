import asyncio

import pytest
from asgiref.sync import iscoroutinefunction, sync_to_async
from django.contrib.auth.models import User
from django.core.exceptions import ImproperlyConfigured, SynchronousOnlyOperation
from django.test import TestCase, override_settings
from django.urls import path
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import permissions as drf_permissions
from rest_framework import serializers as drf_serializers
from rest_framework.renderers import BrowsableAPIRenderer, JSONRenderer

from aiodrf import aio, permissions, serializers, viewsets
from aiodrf.aio import _classify
from aiodrf.compat import FETCH_RAISE, FieldFetchBlocked
from aiodrf.contrib.builtin.prefetch import related_lookups
from aiodrf.response import Response
from aiodrf.test import APIClient, AsyncAPIClient, AsyncAPIRequestFactory, count_hops
from aiodrf.utils import Impl, async_safe, bridge_base, is_pure, resolve_pair
from aiodrf.views import APIView
from tests.base import list_errors
from tests.testapp.models import Author, Book
from tests.testapp.serializers import NestedBookSerializer

# -- resolve_pair ---------------------------------------------------------------


@bridge_base
class Base:
    def work(self):
        return "base"

    async def awork(self):
        return "abase"


class SyncOnly(Base):
    def work(self):
        return "sync"


class AsyncOnly(Base):
    async def awork(self):
        return "async"


class AdrfStyle(Base):
    async def work(self):
        return "adrf"


class BothSyncLast(AsyncOnly):
    def work(self):
        return "sync"


def test_resolve_pair():
    assert resolve_pair(Base, "work", "awork") is Impl.BASE
    assert resolve_pair(SyncOnly, "work", "awork") is Impl.SYNC
    assert resolve_pair(AsyncOnly, "work", "awork") is Impl.ASYNC
    assert resolve_pair(AdrfStyle, "work", "awork") is Impl.SYNC_IS_ASYNC
    # The override closest to the class wins.
    assert resolve_pair(BothSyncLast, "work", "awork") is Impl.SYNC


def test_purity_is_not_inherited_by_overrides():
    class Owner(drf_permissions.IsAuthenticated):
        def has_permission(self, request, view):
            return request.user.profile.active

    assert is_pure(drf_permissions.IsAuthenticated(), "has_permission")
    assert not is_pure(Owner(), "has_permission")

    @async_safe
    class Declared(drf_permissions.BasePermission):
        def has_permission(self, request, view):
            return True

    assert is_pure(Declared(), "has_permission")


# -- Permissions ------------------------------------------------------------------


class Deny(permissions.BasePermission):
    async def ahas_permission(self, request, view):
        return False


class DenyObject(permissions.BasePermission):
    async def ahas_object_permission(self, request, view, obj):
        return False


def test_async_only_permission_is_enforced_by_sync_callers():
    # The browsable API, OPTIONS metadata and schema generation call
    # ``has_permission`` synchronously; it must not fall back to DRF's True.
    request = AsyncAPIRequestFactory().get("/")
    assert Deny().has_permission(request, None) is False
    assert DenyObject().has_object_permission(request, None, object()) is False


class OperatorView(APIView):
    authentication_classes = []

    async def get(self, request):
        return Response({"ok": True})


async def test_operators_mix_sync_and_async_permissions():
    factory = AsyncAPIRequestFactory()
    allow = OperatorView.as_view(permission_classes=[Deny | drf_permissions.AllowAny])
    deny = OperatorView.as_view(permission_classes=[drf_permissions.AllowAny & Deny])
    negated = OperatorView.as_view(permission_classes=[~Deny])
    assert (await allow(factory.get("/"))).status_code == 200
    assert (await deny(factory.get("/"))).status_code == 403
    assert (await negated(factory.get("/"))).status_code == 200


async def test_pure_permissions_need_no_hop():
    view = OperatorView.as_view(permission_classes=[drf_permissions.AllowAny])
    with count_hops() as hops:
        response = await view(AsyncAPIRequestFactory().get("/"))
    assert response.status_code == 200
    assert hops.count == 0, hops.calls


# -- Views --------------------------------------------------------------------------


class Plain(APIView):
    authentication_classes = []
    permission_classes = []

    async def get(self, request):
        return Response({"ok": True})


def test_views_are_coroutine_functions():
    assert iscoroutinefunction(Plain.as_view())
    viewset_view = viewsets.ViewSet.as_view({"get": "list"})
    assert iscoroutinefunction(viewset_view)
    assert viewset_view.csrf_exempt


@extend_schema_view(list=extend_schema(description="Documented"))
class DocumentedViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Author.objects.all()
    serializer_class = drf_serializers.Serializer


class ExtendSchemaViewTests(TestCase):
    @pytest.mark.aiodrf_settings(SERIALIZER_BACKEND_FALLBACK="drf")
    async def test_spectacular_wrapped_async_actions_still_await(self):
        view = DocumentedViewSet.as_view({"get": "list"})
        response = await view(AsyncAPIRequestFactory().get("/"))
        assert response.status_code == 200


# -- Response rendering --------------------------------------------------------------


def test_json_responses_render_inline():
    response = Response({"a": 1})
    response.accepted_renderer = JSONRenderer()
    response.accepted_media_type = "application/json"
    response.renderer_context = {}
    # Django's async handler awaits a coroutine-function ``render``.
    assert iscoroutinefunction(response.render)
    # Sync callers (WSGI handler, test client, cache middleware) get the
    # rendered response back.
    assert response.render() is response
    assert response.content == b'{"a":1}'


async def test_awaiting_render_renders_on_the_loop():
    response = Response({"a": 1})
    response.accepted_renderer = JSONRenderer()
    response.accepted_media_type = "application/json"
    response.renderer_context = {}
    assert await response.render() is response
    assert response.is_rendered


def test_browsable_api_renders_in_a_thread():
    response = Response({"a": 1})
    response.accepted_renderer = BrowsableAPIRenderer()
    assert not iscoroutinefunction(response.render)


def test_responses_pickle():
    import pickle

    response = Response({"a": 1})
    response.accepted_renderer = JSONRenderer()
    response.accepted_media_type = "application/json"
    response.renderer_context = {}
    response.render()
    assert pickle.loads(pickle.dumps(response)).content == b'{"a":1}'


# -- Serializers ------------------------------------------------------------------------


async def title_not_reserved(value):
    await asyncio.sleep(0)
    if value == "reserved":
        raise serializers.ValidationError("Reserved title.")


class AsyncValidatorSerializer(serializers.Serializer):
    title = serializers.CharField(validators=[title_not_reserved])
    count = serializers.IntegerField(min_value=0)


class BookListSerializer(serializers.Serializer):
    books = AsyncValidatorSerializer(many=True)


class AsyncSerializerTests(TestCase):
    async def test_async_field_validators(self):
        serializer = AsyncValidatorSerializer(data={"title": "reserved", "count": -1})
        assert not await serializer.ais_valid()
        assert serializer.errors == {
            "title": ["Reserved title."],
            "count": ["Ensure this value is greater than or equal to 0."],
        }

    async def test_nested_list_errors(self):
        data = {
            "books": [{"title": "ok", "count": 1}, {"title": "reserved", "count": 1}]
        }
        serializer = BookListSerializer(data=data)
        assert not await serializer.ais_valid()
        assert serializer.errors == {
            "books": list_errors({1: {"title": ["Reserved title."]}}, 2)
        }

    async def test_pure_validation_needs_no_hop(self):
        serializer = drf_serializers.Serializer(data={})
        with count_hops() as hops:
            assert await aio.is_valid(serializer)
        assert hops.count == 0

    async def test_adata_both_spellings(self):
        author = await Author.objects.acreate(name="Ursula")

        class AuthorSerializer(serializers.ModelSerializer):
            class Meta:
                model = Author
                fields = ["id", "name"]

        assert await AuthorSerializer(author).adata() == {
            "id": author.pk,
            "name": "Ursula",
        }
        assert await AuthorSerializer(author).adata == {
            "id": author.pk,
            "name": "Ursula",
        }

    async def test_sync_bridge_from_threads(self):
        # The browsable API validates synchronously inside a thread.
        serializer = AsyncValidatorSerializer(data={"title": "reserved", "count": 1})
        valid = await sync_to_async(serializer.is_valid)()
        assert not valid
        assert serializer.errors == {"title": ["Reserved title."]}

    def test_sync_override_with_async_hooks_is_rejected(self):
        class Broken(serializers.Serializer):
            title = serializers.CharField()

            def to_internal_value(self, data):
                return super().to_internal_value(data)

            async def validate_title(self, value):
                return value

        with pytest.raises(ImproperlyConfigured, match="ato_internal_value"):
            _classify.plan_for(Broken(data={}))

    @pytest.mark.aiodrf_settings(
        REPRESENTATION_MODE="thread", SERIALIZER_BACKEND_FALLBACK="drf"
    )
    async def test_representation_runs_once_where_it_is_configured(self):
        author = await Author.objects.acreate(name="Ursula")
        await Book.objects.acreate(title="A", isbn="1", author=author)

        class LazyAuthor(drf_serializers.ModelSerializer):
            author = drf_serializers.StringRelatedField()

            class Meta:
                model = Book
                fields = ["title", "author"]

        # The default: one hop, in which the unloaded relation is simply read.
        book = await Book.objects.aget(isbn="1")
        with count_hops() as hops:
            assert (await aio.data(LazyAuthor(book)))["author"] == "Ursula"
        assert hops.calls == ["try_data"]

        # ``inline`` is an assertion that the instances are loaded. When that
        # is wrong Django says so; nothing is repeated in a thread.
        book = await Book.objects.aget(isbn="1")
        with override_settings(AIODRF={"REPRESENTATION_MODE": "inline"}):
            with pytest.raises(SynchronousOnlyOperation):
                await aio.data(LazyAuthor(book))
            loaded = await Book.objects.select_related("author").aget(isbn="1")
            with count_hops() as hops:
                assert (await aio.data(LazyAuthor(loaded)))["author"] == "Ursula"
            assert hops.count == 0


# -- Authentication ----------------------------------------------------------------------


class SessionView(APIView):
    async def get(self, request):
        return Response({"user": (await request.auser()).get_username()})

    async def post(self, request):
        return Response({"user": request.user.get_username()})


urlpatterns = [path("session/", SessionView.as_view())]


@override_settings(ROOT_URLCONF=__name__)
class SessionAuthenticationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("alice", password="pw")

    async def test_session_and_csrf(self):
        client = AsyncAPIClient(enforce_csrf_checks=True)
        await client.aforce_login(self.user)
        with count_hops() as hops:
            response = await client.get("/session/")
        assert response.data == {"user": "alice"}
        assert hops.count == 0, hops.calls
        response = await client.post("/session/", {})
        assert response.status_code == 403
        assert "CSRF" in response.data["detail"]

    def test_session_through_wsgi(self):
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(self.user)
        assert client.get("/session/").data == {"user": "alice"}
        assert client.post("/session/", {}).status_code == 403


# -- Querysets ---------------------------------------------------------------------------


def test_related_lookups():
    select, prefetch = related_lookups(NestedBookSerializer(), Book)
    assert select == ["author"]
    assert prefetch == ["tags"]


@pytest.mark.skipif(FETCH_RAISE is None, reason="Fetch modes need Django 6.1")
class FetchModeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        author = Author.objects.create(name="Ursula")
        Book.objects.create(title="A", isbn="1", author=author)

    @override_settings(AIODRF={"FETCH_MODE": "raise"})
    async def test_raise_catches_lazy_loads(self):
        class Lazy(drf_serializers.ModelSerializer):
            author = drf_serializers.StringRelatedField()

            class Meta:
                model = Book
                fields = ["author"]

        class LazyViewSet(viewsets.ReadOnlyModelViewSet):
            queryset = Book.objects.all()
            serializer_class = Lazy

        view = LazyViewSet.as_view({"get": "list"})
        with pytest.raises(FieldFetchBlocked):
            await view(AsyncAPIRequestFactory().get("/"))


async def test_denied_before_handler():
    class Guarded(APIView):
        authentication_classes = []
        permission_classes = [Deny]

        async def get(self, request):  # pragma: no cover
            raise AssertionError("handler must not run")

    response = await Guarded.as_view()(AsyncAPIRequestFactory().get("/"))
    assert response.status_code == 403
    assert response.exception is True


def test_the_purity_answer_is_kept_and_follows_later_declarations():
    # ``is_pure`` is decided once per class and method; declaring more,
    # by any of the registration functions or the setting, is seen at once.
    from aiodrf.utils import register_pure, register_pure_method

    class Later(drf_permissions.BasePermission):
        def has_permission(self, request, view):
            return True

    class Method(drf_permissions.BasePermission):
        def has_permission(self, request, view):
            return True

    class Setting(drf_permissions.BasePermission):
        def has_permission(self, request, view):
            return True

    for klass in (Later, Method, Setting):
        assert not is_pure(klass(), "has_permission")
    register_pure(Later)
    register_pure_method(Method, "has_permission")
    with override_settings(AIODRF={"PURE_POLICIES": [Setting]}):
        assert is_pure(Setting(), "has_permission")
    assert not is_pure(Setting(), "has_permission")
    assert is_pure(Later(), "has_permission")
    assert is_pure(Method(), "has_permission")
