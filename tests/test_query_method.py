"""
The HTTP QUERY method (RFC 10008) on aiodrf views.

Django dispatches QUERY from the release that merges ticket #37232; until
then aiodrf adds it to ``APIView.http_method_names`` (``aiodrf.compat``).
Section numbers refer to RFC 10008.
"""

import json
import os
import subprocess
import sys
import textwrap

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import path
from rest_framework import parsers
from rest_framework.permissions import (
    AllowAny,
    DjangoModelPermissions,
    IsAuthenticatedOrReadOnly,
)
from rest_framework.throttling import AnonRateThrottle

from aiodrf import compat, viewsets
from aiodrf.authentication import SessionAuthentication
from aiodrf.cache import cache_page
from aiodrf.decorators import action
from aiodrf.response import Response
from aiodrf.routers import SimpleRouter
from aiodrf.test import APIClient, AsyncAPIClient, AsyncAPIRequestFactory, count_hops
from aiodrf.views import APIView
from tests.base import both_transports
from tests.testapp.models import Author

queried = []


def names(data):
    # JSON content is a dict, form content a QueryDict.
    return data.getlist("names") if hasattr(data, "getlist") else data["names"]


class Search(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    parser_classes = [parsers.JSONParser, parsers.FormParser]

    async def query(self, request):
        queried.append(request.data)
        found = [
            a.name async for a in Author.objects.filter(name__in=names(request.data))
        ]
        return Response({"names": found})

    async def get(self, request):
        return Response({"names": []})


class SyncSearch(Search):
    def query(self, request):
        queried.append(request.data)
        return Response({"names": names(request.data)})


class ReadOnly(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    async def get(self, request):
        return Response({})


class Versioned(Search):
    async def aget_etag(self, request):
        return "v1"


class Private(Search):
    permission_classes = [IsAuthenticatedOrReadOnly]


class ModelPermissions(Search):
    permission_classes = [DjangoModelPermissions]
    queryset = Author.objects.all()


class Session(Search):
    authentication_classes = [SessionAuthentication]


class Throttle(AnonRateThrottle):
    rate = "1/min"


class Throttled(Search):
    throttle_classes = [Throttle]


class Authors(viewsets.ViewSet):
    authentication_classes = []
    permission_classes = [AllowAny]

    @action(detail=False, methods=["query"])
    async def search(self, request):
        return Response({"received": request.data})


router = SimpleRouter()
router.register("authors", Authors, basename="authors")

urlpatterns = [
    path("cached-search/", cache_page(30)(Search.as_view())),
    path("search/", Search.as_view()),
    path("search/sync/", SyncSearch.as_view()),
    path("read-only/", ReadOnly.as_view()),
    path("versioned/", Versioned.as_view()),
    path("private/", Private.as_view()),
    path("model-permissions/", ModelPermissions.as_view()),
    path("session/", Session.as_view()),
    path("throttled/", Throttled.as_view()),
    *router.urls,
]

JSON = "application/json"


class _QueryMethodTests:
    @classmethod
    def setUpClass(cls):
        cls.enterClassContext(override_settings(ROOT_URLCONF=__name__))
        super().setUpClass()

    def setUp(self):
        super().setUp()
        queried.clear()
        Author.objects.bulk_create([Author(name="Ada"), Author(name="Bo")])

    async def query(self, path, body=b"", content_type=JSON, **extra):
        if isinstance(body, dict):
            body = json.dumps(body).encode()
        # The test client sends application/octet-stream unless told otherwise.
        extra["content_type"] = content_type or ""
        if self.transport == "asgi":
            return await self.client.generic("QUERY", path, body, **_asgi(extra))
        return await sync_to_async(self.client.generic)("QUERY", path, body, **extra)

    # RFC 10008 section 2: the content defines the query; the method is safe and idempotent.

    async def test_the_query_is_the_request_content(self):
        for url in ("/search/", "/search/sync/"):
            response = await self.query(url, {"names": ["Bo"]})
            assert response.status_code == 200, response.content
            assert response.json() == {"names": ["Bo"]}
        assert queried == [{"names": ["Bo"]}] * 2

    async def test_form_content_is_a_query_too(self):
        response = await self.query(
            "/search/sync/",
            b"names=Ada",
            content_type="application/x-www-form-urlencoded",
        )
        assert response.json() == {"names": ["Ada"]}

    async def test_a_viewset_action_can_answer_query(self):
        response = await self.query("/authors/search/", {"q": 1})
        assert response.json() == {"received": {"q": 1}}
        other = await self.api("get", "/authors/search/")
        assert other.status_code == 405
        assert set(other["Allow"].split(", ")) == {"QUERY", "OPTIONS"}

    # RFC 10008 sections 2 and 2.1: media type and content must be present and consistent.

    async def test_missing_media_type_is_a_400(self):
        response = await self.query("/search/", b'{"names": []}', content_type=None)
        assert response.status_code == 400
        assert queried == []

    async def test_an_unsupported_media_type_is_a_415_listing_the_supported_ones(self):
        response = await self.query("/search/", b"<q/>", content_type="application/xml")
        assert response.status_code == 415
        assert (
            response["Accept-Query"]
            == "application/json, application/x-www-form-urlencoded"
        )
        assert queried == []

    async def test_content_inconsistent_with_its_media_type_is_a_400(self):
        response = await self.query("/search/", b'{"names": [')
        assert response.status_code == 400
        assert queried == []

    async def test_a_query_without_content_is_a_400(self):
        response = await self.query("/search/", b"")
        assert response.status_code == 400
        assert queried == []

    async def test_an_unacceptable_response_type_is_a_406(self):
        response = await self.query("/search/", {"names": []}, HTTP_ACCEPT="text/csv")
        assert response.status_code == 406

    # RFC 10008 section 3: Accept-Query advertises the query formats.

    async def test_options_advertises_query_and_its_media_types(self):
        response = await self.api("options", "/search/")
        assert "QUERY" in response["Allow"]
        assert (
            response["Accept-Query"]
            == "application/json, application/x-www-form-urlencoded"
        )
        other = await self.api("options", "/read-only/")
        assert "QUERY" not in other["Allow"]
        assert "Accept-Query" not in other

    # Only views that implement it answer QUERY.

    async def test_a_view_without_a_query_handler_answers_405(self):
        response = await self.query("/read-only/", {"names": []})
        assert response.status_code == 405
        assert "QUERY" not in response["Allow"]

    async def test_head_is_not_query(self):
        response = await self.api("head", "/authors/search/")
        assert response.status_code == 405

    # RFC 10008 section 2.6: conditional requests are evaluated like a GET.

    async def test_a_matching_etag_answers_304(self):
        response = await self.query(
            "/versioned/", {"names": []}, HTTP_IF_NONE_MATCH='"v1"'
        )
        assert response.status_code == 304
        assert response["ETag"] == '"v1"'
        response = await self.query("/versioned/", {"names": []})
        assert response.status_code == 200
        assert response["ETag"] == '"v1"'

    async def test_a_failed_if_match_answers_412(self):
        response = await self.query("/versioned/", {"names": []}, HTTP_IF_MATCH='"v0"')
        assert response.status_code == 412

    # What DRF and Django decide stays theirs.

    async def test_drfs_safe_methods_do_not_include_query(self):
        # ``IsAuthenticatedOrReadOnly`` reads ``SAFE_METHODS``: an anonymous
        # QUERY is refused, the conservative reading.
        response = await self.query("/private/", {"names": []})
        assert response.status_code in (401, 403)

    async def test_django_model_permissions_have_no_rule_for_query(self):
        admin = await sync_to_async(User.objects.create_superuser)(
            "admin", "a@example.org", "pw"
        )
        self.client.force_authenticate(admin)
        response = await self.query("/model-permissions/", {"names": []})
        assert response.status_code == 405

    async def test_csrf_protects_query_under_session_authentication(self):
        user = await sync_to_async(User.objects.create_user)("user", password="pw")
        await sync_to_async(self.client.force_login)(user)
        self.client.handler.enforce_csrf_checks = True
        response = await self.query("/session/", {"names": []})
        assert response.status_code == 403
        assert "CSRF" in response.json()["detail"]

    async def test_throttles_apply(self):
        first = await self.query("/throttled/", {"names": []})
        second = await self.query("/throttled/", {"names": []})
        assert (first.status_code, second.status_code) == (200, 429)

    async def test_page_cache_does_not_combine_query_bodies(self):
        for name in ("Ada", "Bo", "Ada"):
            response = await self.query("/cached-search/", {"names": [name]})
            assert response.status_code == 200
            assert response.json() == {"names": [name]}
        assert len(queried) == 3  # Django's GET/HEAD cache bypasses QUERY.


def _asgi(extra):
    return {key.removeprefix("HTTP_"): value for key, value in extra.items()}


both_transports(_QueryMethodTests)


class ClientTests(TestCase):
    async def test_the_async_client_and_factory_send_query(self):
        await Author.objects.acreate(name="Ada")
        request = AsyncAPIRequestFactory().query("/", {"names": ["Ada"]}, format="json")
        with count_hops() as hops:
            response = await Search.as_view()(request)
        assert response.data == {"names": ["Ada"]}
        # JSON parsed on the loop, the query awaited: no thread hop.
        assert hops.count == 0, hops.calls
        with override_settings(ROOT_URLCONF=__name__):
            response = await AsyncAPIClient().query(
                "/search/", {"names": ["Ada"]}, format="json"
            )
        assert response.json() == {"names": ["Ada"]}

    def test_the_sync_client_reaches_it_with_generic(self):
        with override_settings(ROOT_URLCONF=__name__):
            response = APIClient().generic(
                "QUERY", "/search/sync/", '{"names": []}', JSON
            )
        assert response.status_code == 200


class GateTests(TestCase):
    def test_aiodrf_adds_query_only_while_django_does_not_dispatch_it(self):
        from django.views import View

        assert compat.DJANGO_HAS_QUERY is ("query" in View.http_method_names)
        assert "query" in APIView.http_method_names
        assert APIView.http_method_names.count("query") == 1

    def test_with_a_django_that_dispatches_query_aiodrf_adds_nothing(self):
        # A fresh interpreter in which Django's ``View`` knows QUERY, as it
        # will once ticket #37232 is released.
        code = textwrap.dedent(
            """
            import django
            from django.conf import settings
            settings.configure(INSTALLED_APPS=["django.contrib.contenttypes",
                "django.contrib.auth", "rest_framework", "aiodrf"])
            from django.views import View
            View.http_method_names = [*View.http_method_names, "query"]
            django.setup()
            from rest_framework.views import APIView as DRFView
            from aiodrf import compat
            from aiodrf.views import APIView
            assert compat.DJANGO_HAS_QUERY
            assert APIView.http_method_names is DRFView.http_method_names
            print("ok")
            """
        )
        env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        assert result.stdout.strip() == "ok", result.stderr


@pytest.mark.parametrize(
    ("media_types", "header"),
    [
        (["application/json"], "application/json"),
        (["application/vnd.api+json", "*/*"], "application/vnd.api+json, */*"),
        (["3gpp/x"], '"3gpp/x"'),
        (["application/sql; charset=UTF-8"], 'application/sql;charset="UTF-8"'),
        (
            ['application/json; profile="https://example.test/a;b"'],
            'application/json;profile="https://example.test/a;b"',
        ),
        (['text/x; Label="say \\"hi\\""'], 'text/x;label="say \\"hi\\""'),
    ],
)
def test_accept_query_is_a_structured_field_list(media_types, header):
    from aiodrf.views import accept_query

    assert accept_query(media_types) == header


class SchemaTests(TestCase):
    """drf-spectacular builds a request per method with DRF's factory, which has no ``query()``."""

    def schema(self, **spectacular):
        from drf_spectacular.generators import SchemaGenerator
        from drf_spectacular.settings import patched_settings

        with override_settings(ROOT_URLCONF=__name__), patched_settings(spectacular):
            return SchemaGenerator().get_schema(request=None, public=True)

    def test_spectacular_cannot_document_a_query_handler(self):
        # If this fails, drf-spectacular learned QUERY: revisit the hook below.
        with pytest.raises(AttributeError, match="query"):
            self.schema()

    def test_the_hook_leaves_query_out_and_documents_the_rest(self):
        from drf_spectacular.validation import validate_schema

        hook = "aiodrf.contrib.spectacular.hooks.preprocess_exclude_query_method"
        schema = self.schema(PREPROCESSING_HOOKS=[hook])
        validate_schema(schema)
        assert "/authors/search/" not in schema["paths"]
        assert list(schema["paths"]["/search/"]) == ["get"]
