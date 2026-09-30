"""
Where code runs.

Synchronous code written for DRF runs in a worker thread, once: it is never
tried on the event loop first. Only code that is known not to block runs on
the loop, and that knowledge has to cover what the code calls.
"""

import hashlib
import inspect
import threading

import django_filters
import pytest
from django.core.validators import MinValueValidator
from django.test import TestCase, override_settings
from django.urls import path
from rest_framework import (
    authentication,
    filters,
    generics,
    mixins,
    negotiation,
    parsers,
    renderers,
    throttling,
    versioning,
)
from rest_framework import permissions as drf_permissions
from rest_framework import serializers as drf_serializers
from rest_framework.utils import encoders

from aiodrf import serializers, viewsets
from aiodrf.contrib.django_filters import DjangoFilterBackend
from aiodrf.request import Request
from aiodrf.response import Response
from aiodrf.test import AsyncAPIClient, AsyncAPIRequestFactory, count_hops
from aiodrf.utils import async_safe, is_pure
from aiodrf.views import APIView
from tests.base import both_transports
from tests.testapp.models import Author, Book
from tests.testapp.serializers import AuthorSerializer


class Public:
    authentication_classes = []
    permission_classes = [drf_permissions.AllowAny]


class AuthorViewSet(Public, viewsets.ModelViewSet):
    queryset = Author.objects.all()
    serializer_class = AuthorSerializer


# -- Lifecycle hooks that are not pairs ------------------------------------------


class QueryingVersioning(versioning.QueryParameterVersioning):
    def determine_version(self, request, *args, **kwargs):
        Author.objects.exists()
        return super().determine_version(request, *args, **kwargs)


class QueryingNegotiation(negotiation.DefaultContentNegotiation):
    def select_renderer(self, request, renderers, format_suffix=None):
        Author.objects.exists()
        return super().select_renderer(request, renderers, format_suffix)


class VersionedViewSet(AuthorViewSet):
    versioning_class = QueryingVersioning


class NegotiatedViewSet(AuthorViewSet):
    content_negotiation_class = QueryingNegotiation


class RenderersViewSet(AuthorViewSet):
    def get_renderers(self):
        Author.objects.exists()
        return super().get_renderers()


class ParsersViewSet(AuthorViewSet):
    def get_parsers(self):
        Author.objects.exists()
        return super().get_parsers()


class FinalizedViewSet(AuthorViewSet):
    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["X-Authors"] = str(Author.objects.count())
        return response


class HandledViewSet(AuthorViewSet):
    permission_classes = [drf_permissions.IsAuthenticated]

    def get_exception_handler_context(self):
        return {
            **super().get_exception_handler_context(),
            "authors": Author.objects.count(),
        }


# -- "Pure" classes that call overridable hooks ------------------------------------


class TenantSearch(filters.SearchFilter):
    def get_search_fields(self, view, request):
        return ["name"] if Author.objects.exists() else []


class SearchViewSet(AuthorViewSet):
    filter_backends = [TenantSearch]
    search_fields = ["name"]


class QueryingFieldsSerializer(drf_serializers.ModelSerializer):
    class Meta:
        model = Book
        fields = ["id", "title"]

    def get_fields(self):
        Author.objects.exists()
        return super().get_fields()


class OrderedViewSet(Public, viewsets.ReadOnlyModelViewSet):
    # ``OrderingFilter`` builds the view's serializer to learn its fields.
    queryset = Book.objects.all()
    serializer_class = QueryingFieldsSerializer
    filter_backends = [filters.OrderingFilter]


class QueryingFilterSet(django_filters.FilterSet):
    class Meta:
        model = Author
        fields = ["name"]

    def filter_queryset(self, queryset):
        # A documented extension point of django-filter.
        queryset.exists()
        return super().filter_queryset(queryset)


class FilteredViewSet(AuthorViewSet):
    filter_backends = [DjangoFilterBackend]
    filterset_class = QueryingFilterSet


class PlanThrottle(throttling.UserRateThrottle):
    calls = []

    def get_rate(self):
        self.calls.append(threading.get_ident())
        return "100/min" if Author.objects.exists() else "1/min"


class ThrottledViewSet(AuthorViewSet):
    throttle_classes = [PlanThrottle]


class SummarySerializer(serializers.ModelSerializer):
    summary = serializers.SerializerMethodField()

    class Meta:
        model = Book
        fields = ["title", "summary"]

    async def get_summary(self, obj):
        return await obj.asummary()

    def to_representation(self, instance):
        # Written for DRF around an async field added later.
        return {**super().to_representation(instance), "wrapped": True}


class SummaryViewSet(Public, viewsets.ReadOnlyModelViewSet):
    queryset = Book.objects.all()
    serializer_class = SummarySerializer


NAMES = {
    "versioned": VersionedViewSet,
    "negotiated": NegotiatedViewSet,
    "renderers": RenderersViewSet,
    "parsers": ParsersViewSet,
    "finalized": FinalizedViewSet,
    "handled": HandledViewSet,
    "search": SearchViewSet,
    "ordered": OrderedViewSet,
    "filtered": FilteredViewSet,
    "throttled": ThrottledViewSet,
    "summary": SummaryViewSet,
    "plain": AuthorViewSet,
}
urlpatterns = [
    path(
        f"{name}/",
        viewset.as_view(
            {"get": "list"}
            if name in ("ordered", "summary")
            else {"get": "list", "post": "create"}
        ),
    )
    for name, viewset in NAMES.items()
]
urls = override_settings(ROOT_URLCONF=__name__)


@both_transports
class _LifecycleTests:
    @classmethod
    def setUpTestData(cls):
        author = Author.objects.create(name="Ursula")
        Book.objects.create(title="A", isbn="1", author=author)

    @urls
    async def test_hooks_that_query(self):
        for name in (
            "versioned",
            "negotiated",
            "renderers",
            "parsers",
            "search",
            "filtered",
        ):
            with self.subTest(hook=name):
                response = await self.api(
                    "get", f"/{name}/", data={"name": "Ursula", "search": "U"}
                )
                assert response.status_code == 200, name
                assert [author["name"] for author in response.data] == ["Ursula"]

    @urls
    async def test_finalize_response_that_queries(self):
        response = await self.api("get", "/finalized/")
        assert response.status_code == 200
        assert response["X-Authors"] == "1"

    @urls
    async def test_exception_handler_context_that_queries(self):
        assert (await self.api("get", "/handled/")).status_code == 403

    @urls
    async def test_ordering_filter_builds_the_serializer_in_the_worker(self):
        response = await self.api("get", "/ordered/", data={"ordering": "title"})
        assert response.status_code == 200, response.data

    @pytest.mark.aiodrf_settings(REPRESENTATION_MODE="thread")
    @urls
    async def test_sync_to_representation_around_async_fields(self):
        response = await self.api("get", "/summary/")
        assert response.status_code == 200
        assert response.data == [
            {"title": "A", "summary": "A (100 pages)", "wrapped": True}
        ]


class ExactlyOnceTests(TestCase):
    @urls
    async def test_throttle_is_built_once_and_off_the_loop(self):
        PlanThrottle.calls.clear()
        response = await AsyncAPIClient().get("/throttled/")
        assert response.status_code == 200
        assert len(PlanThrottle.calls) == 1
        assert PlanThrottle.calls[0] != threading.get_ident()

    @urls
    async def test_plain_viewset_budget(self):
        await Author.objects.acreate(name="Ursula")
        with count_hops() as hops:
            response = await AsyncAPIClient().get("/plain/")
        assert response.status_code == 200
        assert hops.calls == ["ListModelMixin._list"]
        with count_hops() as hops:
            response = await AsyncAPIClient().post("/plain/", {"name": "Other"})
        assert response.status_code == 201
        # Parsing, validation, the save and the representation: one hop.
        assert hops.calls == ["CreateModelMixin._create"]


# -- Parsers ------------------------------------------------------------------------


class RecordingJSONParser(parsers.JSONParser):
    def parse(self, *args, **kwargs):
        self.thread = threading.get_ident()
        return super().parse(*args, **kwargs)


class ParserPlacementTests(TestCase):
    async def test_a_parser_subclass_runs_off_the_loop(self):
        parser = RecordingJSONParser()
        raw = AsyncAPIRequestFactory().post("/", {"value": 1}, format="json")
        request = Request(raw, parsers=[parser])
        assert await request.adata() == {"value": 1}
        assert parser.thread != threading.get_ident()

    async def test_drfs_parser_runs_inline_for_a_body_in_memory(self):
        raw = AsyncAPIRequestFactory().post("/", {"value": 1}, format="json")
        request = Request(raw, parsers=[parsers.JSONParser()])
        with count_hops() as hops:
            assert await request.adata() == {"value": 1}
        assert hops.count == 0

    async def test_a_body_django_spooled_to_disk_is_read_in_a_thread(self):
        raw = AsyncAPIRequestFactory().post("/", {"value": "x" * 64}, format="json")
        request = Request(raw, parsers=[parsers.JSONParser()])
        with override_settings(FILE_UPLOAD_MAX_MEMORY_SIZE=16), count_hops() as hops:
            assert (await request.adata())["value"] == "x" * 64
        assert hops.count == 1


# -- Purity -------------------------------------------------------------------------


class CustomEncoder(encoders.JSONEncoder):
    pass


class EncoderRenderer(renderers.JSONRenderer):
    encoder_class = CustomEncoder


class CompactRenderer(renderers.JSONRenderer):
    compact = False  # plain data


class EncoderLookup:
    # A descriptor: reading the attribute runs this, and it is no callable.
    def __get__(self, instance, owner=None):
        return CustomEncoder


class LookedUpEncoderRenderer(renderers.JSONRenderer):
    encoder_class = EncoderLookup()


class IsOwner(drf_permissions.IsAuthenticated):
    def has_object_permission(self, request, view, obj):
        return obj.owner_id == request.user.pk


@async_safe
class DeclaredSearch(filters.SearchFilter):
    def get_search_fields(self, view, request):
        return ["name"]


class UndeclaredChild(DeclaredSearch):
    def get_search_terms(self, request):
        return Author.objects.values_list("name", flat=True)


def test_purity_covers_what_a_method_calls():
    assert is_pure(renderers.JSONRenderer(), "render")
    assert is_pure(CompactRenderer(), "render")
    # ``encoder_class`` is code that ``render`` runs.
    assert not is_pure(EncoderRenderer(), "render")
    assert not is_pure(LookedUpEncoderRenderer(), "render")
    # DRF's filters call hooks projects override; they are not registered.
    assert not is_pure(filters.SearchFilter(), "filter_queryset")
    assert not is_pure(TenantSearch(), "filter_queryset")
    # A declared class vouches for its own methods, not for a subclass's.
    assert is_pure(DeclaredSearch(), "get_search_fields")
    assert not is_pure(UndeclaredChild(), "get_search_fields")


def test_leaf_permissions_stay_pure_in_subclasses_that_add_methods():
    # ``IsAuthenticated.has_permission`` calls nothing a subclass could change.
    assert is_pure(IsOwner(), "has_permission")
    assert not is_pure(IsOwner(), "has_object_permission")


class OwnerView(APIView):
    authentication_classes = []
    permission_classes = [drf_permissions.AllowAny]

    async def get(self, request):
        return Response({"ok": True})


async def test_pure_lifecycle_costs_no_hop():
    with count_hops() as hops:
        response = await OwnerView.as_view()(AsyncAPIRequestFactory().get("/"))
    assert response.status_code == 200
    assert hops.count == 0, hops.calls


# -- The bodies that mirror DRF -------------------------------------------------------

# aiodrf's synchronous action bodies (``aiodrf.mixins``, ``GenericAPIView._object``)
# follow these DRF methods line by line. The digests are of DRF 3.16 to 3.18,
# whose sources are identical. When one changes, read the new method, bring
# the body in line and add the digest.
MIRRORED = {
    (mixins.CreateModelMixin, "create"): {"570fb3ac5834"},
    (mixins.ListModelMixin, "list"): {"9c3f4f0d20df"},
    (mixins.RetrieveModelMixin, "retrieve"): {"0779d28b53a6"},
    (mixins.UpdateModelMixin, "update"): {"ccff3939c5dc"},
    (mixins.DestroyModelMixin, "destroy"): {"23caf043f721"},
    (generics.GenericAPIView, "get_object"): {"b468a66ac12d"},
    # ``aiodrf.contrib.permissions`` mirrors these in ``ahas_permission``.
    (drf_permissions.DjangoModelPermissions, "has_permission"): {"a9e251c060e5"},
    # 3.16.0 formats the same assertion message with ``str.format``.
    (drf_permissions.DjangoModelPermissions, "_queryset"): {
        "c70d406cde36",
        "66cdeadabf20",
    },
}


def test_mirrored_drf_methods_have_not_changed():
    for (owner, name), known in MIRRORED.items():
        source = inspect.getsource(getattr(owner, name))
        digest = hashlib.sha256(source.encode()).hexdigest()[:12]
        assert digest in known, f"DRF changed {owner.__name__}.{name}(): {digest}"


# -- Constructors and callbacks ------------------------------------------------------


class Placed:
    """Records the thread a constructor or callback ran in."""

    threads = []

    @classmethod
    def record(cls):
        cls.threads.append(threading.get_ident())


class ConstructedPermission(drf_permissions.AllowAny):
    def __init__(self):
        Placed.record()


class ConstructedParser(parsers.JSONParser):
    def __init__(self):
        Placed.record()


class ConstructedRenderer(renderers.JSONRenderer):
    def __init__(self):
        Placed.record()


class ConstructedAuthentication(authentication.BaseAuthentication):
    def __init__(self):
        Placed.record()

    def authenticate(self, request):
        return None


class PermissionBuiltView(Public, APIView):
    permission_classes = [ConstructedPermission]

    async def get(self, request):
        return Response({"ok": True})


class ParserBuiltView(Public, APIView):
    parser_classes = [ConstructedParser]

    async def post(self, request):
        return Response(await request.adata())


class RendererBuiltView(Public, APIView):
    renderer_classes = [ConstructedRenderer]

    async def get(self, request):
        return Response({"ok": True})


class AuthenticatorBuiltView(Public, APIView):
    authentication_classes = [ConstructedAuthentication]

    async def get(self, request):
        return Response({"ok": True})


def limit_from_settings():
    Placed.record()
    return 3


class LimitedSerializer(drf_serializers.Serializer):
    value = drf_serializers.IntegerField(
        validators=[MinValueValidator(limit_from_settings)]
    )


urlpatterns += [
    path("built/permission/", PermissionBuiltView.as_view()),
    path("built/parser/", ParserBuiltView.as_view()),
    path("built/renderer/", RendererBuiltView.as_view()),
    path("built/authenticator/", AuthenticatorBuiltView.as_view()),
]


@urls
class ConstructorPlacementTests(TestCase):
    """A constructor a project wrote is code: it runs off the loop, once."""

    def setUp(self):
        Placed.threads.clear()

    async def assert_built_off_the_loop(self, method, url, **kwargs):
        response = await getattr(AsyncAPIClient(), method)(url, **kwargs)
        assert response.status_code == 200, response.data
        assert len(Placed.threads) == 1
        assert Placed.threads[0] != threading.get_ident()

    async def test_permission(self):
        await self.assert_built_off_the_loop("get", "/built/permission/")

    async def test_parser(self):
        await self.assert_built_off_the_loop("post", "/built/parser/", data={"a": 1})

    async def test_renderer(self):
        await self.assert_built_off_the_loop("get", "/built/renderer/")

    async def test_authenticator(self):
        await self.assert_built_off_the_loop("get", "/built/authenticator/")

    async def test_a_validator_with_a_callable_limit(self):
        from aiodrf import aio

        serializer = LimitedSerializer(data={"value": 1})
        assert not await aio.is_valid(serializer)
        assert serializer.errors["value"][0].code == "min_value"
        assert len(Placed.threads) == 1
        assert Placed.threads[0] != threading.get_ident()

    async def test_a_constructor_declared_pure_stays_on_the_loop(self):
        from aiodrf.utils import register_pure_method

        register_pure_method(ConstructedPermission, "__init__")
        try:
            with count_hops() as hops:
                response = await AsyncAPIClient().get("/built/permission/")
        finally:
            from aiodrf import utils

            utils._pure.methods.pop(ConstructedPermission, None)
            utils._pure.changed()
        assert response.status_code == 200
        assert hops.calls == []
        assert Placed.threads == [threading.get_ident()]
