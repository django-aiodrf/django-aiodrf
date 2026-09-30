"""
Conditional requests: ``get_etag`` / ``aget_etag`` and ``get_last_modified``
/ ``aget_last_modified`` on the view, evaluated with Django's RFC 9110 logic
after authentication, permissions and throttling, before the handler.
"""

import datetime
import threading

import pytest
from django.core.exceptions import SynchronousOnlyOperation
from django.http import Http404
from django.test import TestCase, override_settings
from django.urls import path
from django.utils.asyncio import async_unsafe
from django.utils.http import http_date
from django.views.decorators.http import condition
from rest_framework import serializers
from rest_framework.permissions import AllowAny, BasePermission, IsAuthenticated
from rest_framework.throttling import AnonRateThrottle

from aiodrf import generics
from aiodrf.response import Response
from aiodrf.test import AsyncAPIRequestFactory, count_hops
from aiodrf.views import APIView
from tests.base import both_transports
from tests.testapp.models import Author

MODIFIED = datetime.datetime(2026, 9, 1, 12, 0, tzinfo=datetime.UTC)
BEFORE = http_date(MODIFIED.timestamp() - 60)
AFTER = http_date(MODIFIED.timestamp() + 60)

handled = []


class Handlers(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    async def get(self, request, pk):
        handled.append("get")
        author = await Author.objects.aget(pk=pk)
        return Response({"name": author.name})

    async def put(self, request, pk):
        handled.append("put")
        await Author.objects.filter(pk=pk).aupdate(name=request.data["name"])
        return Response({"name": request.data["name"]})

    async def post(self, request, pk):
        handled.append("post")
        return Response({}, status=201)


class Document(Handlers):
    async def aget_etag(self, request, pk):
        author = await Author.objects.filter(pk=pk).afirst()
        if author is None:
            raise Http404
        return f"{author.pk}-{author.name}"

    async def aget_last_modified(self, request, pk):
        return MODIFIED


class SyncDocument(Handlers):
    # Written for DRF: synchronous hooks that query.
    def get_etag(self, request, pk):
        author = Author.objects.get(pk=pk)
        return f"{author.pk}-{author.name}"

    def get_last_modified(self, request, pk):
        return MODIFIED.replace(tzinfo=None)  # naive: read as UTC, like Django


class Private(Document):
    permission_classes = [IsAuthenticated]


class Throttle(AnonRateThrottle):
    rate = "1/min"


class Throttled(Document):
    throttle_classes = [Throttle]


class OwnETag(Document):
    async def get(self, request, pk):
        return Response({}, headers={"ETag": '"from-the-handler"'})


urlpatterns = [
    path("documents/<int:pk>/", Document.as_view()),
    path("sync/<int:pk>/", SyncDocument.as_view()),
    path("private/<int:pk>/", Private.as_view()),
    path("throttled/<int:pk>/", Throttled.as_view()),
    path("own/<int:pk>/", OwnETag.as_view()),
]


class _ConditionalTests:
    @classmethod
    def setUpClass(cls):
        cls.enterClassContext(override_settings(ROOT_URLCONF=__name__))
        super().setUpClass()

    def setUp(self):
        super().setUp()
        handled.clear()
        self.author = Author.objects.create(name="Ada")
        self.etag = f'"{self.author.pk}-Ada"'

    async def test_a_response_carries_the_validators(self):
        for prefix in ("documents", "sync"):
            response = await self.api("get", f"/{prefix}/{self.author.pk}/")
            assert response.status_code == 200
            assert response["ETag"] == self.etag
            assert response["Last-Modified"] == http_date(MODIFIED.timestamp())

    async def test_a_matching_etag_answers_304_without_running_the_handler(self):
        for prefix in ("documents", "sync"):
            for header in (self.etag, f"W/{self.etag}", '"other", ' + self.etag, "*"):
                response = await self.api(
                    "get", f"/{prefix}/{self.author.pk}/", HTTP_IF_NONE_MATCH=header
                )
                assert response.status_code == 304, header
                assert response["ETag"] == self.etag
        assert handled == []

    async def test_another_etag_gets_the_representation(self):
        response = await self.api(
            "get", f"/documents/{self.author.pk}/", HTTP_IF_NONE_MATCH='"stale"'
        )
        assert response.status_code == 200
        assert response.json() == {"name": "Ada"}

    async def test_if_modified_since(self):
        url = f"/documents/{self.author.pk}/"
        assert (
            await self.api("get", url, HTTP_IF_MODIFIED_SINCE=AFTER)
        ).status_code == 304
        assert (
            await self.api("get", url, HTTP_IF_MODIFIED_SINCE=BEFORE)
        ).status_code == 200

    async def test_head(self):
        response = await self.api(
            "head", f"/documents/{self.author.pk}/", HTTP_IF_NONE_MATCH=self.etag
        )
        assert response.status_code == 304

    async def test_a_failed_if_match_answers_412_in_drfs_error_format(self):
        response = await self.api(
            "put",
            f"/documents/{self.author.pk}/",
            data={"name": "Bo"},
            format="json",
            HTTP_IF_MATCH='"stale"',
        )
        assert response.status_code == 412
        assert response.json() == {"detail": "Precondition failed."}
        assert handled == []
        assert await Author.objects.filter(name="Ada").aexists()

    async def test_a_matching_if_match_lets_the_write_through(self):
        response = await self.api(
            "put",
            f"/documents/{self.author.pk}/",
            data={"name": "Bo"},
            format="json",
            HTTP_IF_MATCH=self.etag,
        )
        assert response.status_code == 200
        assert handled == ["put"]
        # Validators describe the state before a write; only safe methods get them.
        assert "ETag" not in response

    async def test_if_unmodified_since(self):
        url = f"/documents/{self.author.pk}/"
        kwargs = {"data": {"name": "Bo"}, "format": "json"}
        stale = await self.api("put", url, HTTP_IF_UNMODIFIED_SINCE=BEFORE, **kwargs)
        assert stale.status_code == 412
        current = await self.api("put", url, HTTP_IF_UNMODIFIED_SINCE=AFTER, **kwargs)
        assert current.status_code == 200

    async def test_if_none_match_on_a_write_is_a_failed_precondition(self):
        response = await self.api(
            "post", f"/documents/{self.author.pk}/", HTTP_IF_NONE_MATCH="*"
        )
        assert response.status_code == 412

    async def test_a_missing_resource_is_a_404(self):
        response = await self.api("get", "/documents/999/", HTTP_IF_NONE_MATCH='"x"')
        assert response.status_code == 404

    async def test_permissions_come_first(self):
        response = await self.api(
            "get", f"/private/{self.author.pk}/", HTTP_IF_NONE_MATCH=self.etag
        )
        assert response.status_code in (401, 403)

    async def test_throttling_comes_first(self):
        url = f"/throttled/{self.author.pk}/"
        assert (
            await self.api("get", url, HTTP_IF_NONE_MATCH=self.etag)
        ).status_code == 304
        assert (
            await self.api("get", url, HTTP_IF_NONE_MATCH=self.etag)
        ).status_code == 429

    async def test_an_etag_set_by_the_handler_is_kept(self):
        response = await self.api("get", f"/own/{self.author.pk}/")
        assert response["ETag"] == '"from-the-handler"'


both_transports(_ConditionalTests)


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("validator", ["etag", "last_modified"])
async def test_instance_validator_is_resolved_after_a_class_only_request(
    asynchronous, validator
):
    """Instance configuration must not inherit a cached 'no validators' decision."""
    threads = []

    class Plain(APIView):
        authentication_classes = []
        permission_classes = []

        async def get(self, request):
            return Response({"id": 1})

    request = AsyncAPIRequestFactory().get(
        "/", HTTP_IF_NONE_MATCH='"current"', HTTP_IF_MODIFIED_SINCE=AFTER
    )
    assert (await Plain.as_view()(request)).status_code == 200
    value = "current" if validator == "etag" else MODIFIED

    def sync_hook(request):
        threads.append(threading.get_ident())
        return value

    async def async_hook(request):
        threads.append(threading.get_ident())
        return value

    name = f"{'a' if asynchronous else ''}get_{validator}"
    request = AsyncAPIRequestFactory().get(
        "/",
        **{
            "HTTP_IF_NONE_MATCH" if validator == "etag" else "HTTP_IF_MODIFIED_SINCE": (
                '"current"' if validator == "etag" else AFTER
            )
        },
    )
    with count_hops() as hops:
        response = await Plain.as_view(
            **{name: async_hook if asynchronous else sync_hook}
        )(request)
    assert response.status_code == 304
    assert hops.count == (0 if asynchronous else 1)
    assert len(threads) == 1
    assert (threads[0] == threading.get_ident()) is asynchronous


class CostTests(TestCase):
    def setUp(self):
        handled.clear()
        self.author = Author.objects.create(name="Ada")

    async def call(self, view, method="get", **headers):
        request = getattr(AsyncAPIRequestFactory(), method)("/", **headers)
        with count_hops() as hops:
            response = await view(request, pk=self.author.pk)
        return response, hops

    async def test_async_hooks_cost_no_hop(self):
        response, hops = await self.call(
            Document.as_view(), HTTP_IF_NONE_MATCH=f'"{self.author.pk}-Ada"'
        )
        assert response.status_code == 304
        assert hops.count == 0, hops.calls

    async def test_synchronous_hooks_share_one_hop_off_the_loop(self):
        threads = []

        class Checked(SyncDocument):
            @async_unsafe("etag on loop")
            def get_etag(self, request, pk):
                threads.append(threading.get_ident())
                return super().get_etag(request, pk)

            @async_unsafe("last modified on loop")
            def get_last_modified(self, request, pk):
                threads.append(threading.get_ident())
                return super().get_last_modified(request, pk)

        response, hops = await self.call(
            Checked.as_view(), HTTP_IF_NONE_MATCH=f'"{self.author.pk}-Ada"'
        )
        assert response.status_code == 304
        assert hops.count == 1, hops.calls
        assert len(set(threads)) == 1
        assert threads[0] != threading.get_ident()

    async def test_writes_without_preconditions_do_not_compute_validators(self):
        class Counted(SyncDocument):
            def get_etag(self, request, pk):
                raise AssertionError("computed")

        response, hops = await self.call(Counted.as_view(), "post")
        assert response.status_code == 201
        assert hops.count == 0, hops.calls

    async def test_a_view_without_hooks_pays_nothing(self):
        class Plain(APIView):
            authentication_classes = []
            permission_classes = [AllowAny]

            async def get(self, request, pk):
                return Response({})

        response, hops = await self.call(Plain.as_view(), HTTP_IF_NONE_MATCH="*")
        assert response.status_code == 200
        assert "ETag" not in response
        assert hops.count == 0


class DjangoConditionTests(TestCase):
    """Why the view hooks exist; if this fails, Django changed."""

    async def test_djangos_decorator_computes_the_etag_on_the_event_loop(self):
        def etag(request):
            return str(Author.objects.count())

        @condition(etag_func=etag)
        async def view(request):
            raise AssertionError("not reached")

        with pytest.raises(SynchronousOnlyOperation):
            await view(AsyncAPIRequestFactory().get("/", HTTP_IF_NONE_MATCH='"1"'))


class AuthorFields(serializers.ModelSerializer):
    class Meta:
        model = Author
        fields = ["id", "name"]


class HiddenAuthors(BasePermission):
    def has_object_permission(self, request, view, obj):
        return obj.name != "hidden"


class AuthorDetail(generics.RetrieveAPIView):
    # The guide's example: the validator scopes its lookup like the retrieve.
    queryset = Author.objects.all()
    serializer_class = AuthorFields
    authentication_classes = []
    permission_classes = [HiddenAuthors]

    async def aget_etag(self, request, pk):
        author = await self.aget_object()
        return f"{author.pk}-{author.name}"


class ScopedValidatorTests(TestCase):
    async def test_a_validator_through_aget_object_answers_like_the_retrieve(self):
        visible = await Author.objects.acreate(name="visible")
        hidden = await Author.objects.acreate(name="hidden")
        view = AuthorDetail.as_view()
        cached = await view(
            AsyncAPIRequestFactory().get(
                "/", HTTP_IF_NONE_MATCH=f'"{visible.pk}-visible"'
            ),
            pk=visible.pk,
        )
        assert cached.status_code == 304
        denied = await view(
            AsyncAPIRequestFactory().get(
                "/", HTTP_IF_NONE_MATCH=f'"{hidden.pk}-hidden"'
            ),
            pk=hidden.pk,
        )
        assert denied.status_code == 403
        missing = await view(
            AsyncAPIRequestFactory().get("/", HTTP_IF_NONE_MATCH='"0-x"'), pk=999999
        )
        assert missing.status_code == 404
