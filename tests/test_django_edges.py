"""
Where Django's behaviour depends on the thread, the context or the settings:
session engines and CSRF, request bodies, translations and time zones across
thread hops, synchronous middleware, streaming, ``ATOMIC_REQUESTS`` and
database routers. Wherever DRF has an answer, aiodrf must give the same one.
"""

import datetime
import json
import sys
import tempfile
import zoneinfo
from unittest import mock

import pytest
from asgiref.sync import iscoroutinefunction
from django.contrib.auth.models import User
from django.contrib.sessions.backends.file import SessionStore as FileSessionStore
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connections
from django.http import StreamingHttpResponse
from django.middleware.csrf import get_token
from django.test import TestCase, override_settings
from django.urls import path
from django.utils import timezone, translation
from django.utils.asyncio import async_unsafe
from rest_framework import serializers as drf_serializers
from rest_framework import views as drf_views
from rest_framework import viewsets as drf_viewsets
from rest_framework.authentication import SessionAuthentication
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response as DRFResponse

from aiodrf import aio, viewsets
from aiodrf.compat import DRF_VERSION
from aiodrf.response import Response
from aiodrf.test import APIClient, AsyncAPIClient, count_hops
from aiodrf.views import APIView
from tests.base import both_transports
from tests.testapp.models import Author
from tests.testapp.serializers import AuthorSerializer

urls = override_settings(ROOT_URLCONF=__name__)

# -- Sessions and CSRF -------------------------------------------------------------


class SessionPolicies:
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]


class DRFAccount(SessionPolicies, drf_views.APIView):
    def get(self, request):
        return DRFResponse(
            {"user": request.user.get_username(), "token": get_token(request)}
        )

    def post(self, request):
        return DRFResponse({"user": request.user.get_username()})


class Account(SessionPolicies, APIView):
    async def get(self, request):
        return Response(
            {"user": request.user.get_username(), "token": get_token(request)}
        )

    async def post(self, request):
        return Response({"user": request.user.get_username()})


SESSION_ENGINES = ["db", "cache", "cached_db", "file", "signed_cookies"]


def for_each_session_engine(cls):
    module = sys.modules[cls.__module__]
    for engine in SESSION_ENGINES:
        for csrf_in_session in (False, True):
            name = f"{cls.__name__.removeprefix('_')}_{engine}{'_csrf_in_session' * csrf_in_session}"
            attrs = {
                "__module__": cls.__module__,
                "engine": engine,
                "csrf_in_session": csrf_in_session,
            }
            setattr(module, name, type(name, (cls, TestCase), attrs))
    return cls


@for_each_session_engine
class _SessionTests:
    @classmethod
    def setUpClass(cls):
        cls.enterClassContext(
            override_settings(
                ROOT_URLCONF=__name__,
                SESSION_ENGINE=f"django.contrib.sessions.backends.{cls.engine}",
                SESSION_FILE_PATH=cls.enterClassContext(tempfile.TemporaryDirectory()),
                CSRF_USE_SESSIONS=cls.csrf_in_session,
            )
        )
        # The file backend remembers its directory on the class (Django's
        # own tests reset it the same way).
        cls.forget_session_directory()
        cls.addClassCleanup(cls.forget_session_directory)
        super().setUpClass()

    @staticmethod
    def forget_session_directory():
        if hasattr(FileSessionStore, "_storage_path"):
            del FileSessionStore._storage_path

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("alice", password="pw")

    async def test_aiodrf_authenticates_and_checks_csrf_like_drf(self):
        for prefix in ("drf", "aiodrf"):
            with self.subTest(view=prefix):
                client = AsyncAPIClient(enforce_csrf_checks=True)
                assert (await client.get(f"/{prefix}/account/")).status_code == 403
                await client.aforce_login(self.user)
                account = await client.get(f"/{prefix}/account/")
                assert account.data["user"] == "alice"
                view = DRFAccount if prefix == "drf" else Account
                with mock.patch.object(
                    view, "post", side_effect=AssertionError("CSRF bypass")
                ):
                    rejected = await client.post(f"/{prefix}/account/", {})
                assert rejected.status_code == 403
                assert "CSRF" in rejected.data["detail"]
                accepted = await client.post(
                    f"/{prefix}/account/", {}, HTTP_X_CSRFTOKEN=account.data["token"]
                )
                assert accepted.status_code == 200, accepted.data
                assert accepted.data == {"user": "alice"}

    def test_through_wsgi(self):
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(self.user)
        account = client.get("/aiodrf/account/")
        with mock.patch.object(
            Account, "post", side_effect=AssertionError("CSRF bypass")
        ):
            assert client.post("/aiodrf/account/", {}).status_code == 403
        accepted = client.post(
            "/aiodrf/account/", {}, HTTP_X_CSRFTOKEN=account.data["token"]
        )
        assert accepted.data == {"user": "alice"}


# -- Request bodies ------------------------------------------------------------------


class BodyPolicies:
    authentication_classes = []
    permission_classes = [AllowAny]
    parser_classes = [JSONParser, FormParser, MultiPartParser]


def describe(data, files):
    uploads = {
        name: [upload.name, upload.read().decode()] for name, upload in files.items()
    }
    items = {key: value for key, value in data.items() if key not in uploads}
    return {"data": items, "files": uploads}


class DRFEcho(BodyPolicies, drf_views.APIView):
    def post(self, request):
        return DRFResponse(describe(request.data, request.FILES))


class Echo(BodyPolicies, APIView):
    async def post(self, request):
        return Response(describe(await request.adata(), request.FILES))


@both_transports
class _BodyTests:
    async def both(self, **kwargs):
        drf = await self.api("post", "/drf/echo/", **kwargs)
        aiodrf = await self.api("post", "/aiodrf/echo/", **kwargs)
        assert aiodrf.status_code == drf.status_code, (drf.data, aiodrf.data)
        assert aiodrf.data == drf.data
        return aiodrf

    @urls
    async def test_json_kept_in_memory(self):
        response = await self.both(data={"name": "Ursula"})
        assert response.data == {"data": {"name": "Ursula"}, "files": {}}

    @urls
    @override_settings(FILE_UPLOAD_MAX_MEMORY_SIZE=1024)
    async def test_json_django_spooled_to_disk(self):
        # Django's ASGI handler writes bodies above the limit to a temporary
        # file; reading it back is file I/O.
        response = await self.both(data={"text": "x" * 4096})
        assert response.data["data"] == {"text": "x" * 4096}

    @urls
    async def test_multipart_with_a_file(self):
        def payload():
            return {
                "title": "Draft",
                "upload": SimpleUploadedFile("notes.txt", b"hello"),
            }

        drf = await self.api("post", "/drf/echo/", data=payload(), format="multipart")
        aiodrf = await self.api(
            "post", "/aiodrf/echo/", data=payload(), format="multipart"
        )
        assert aiodrf.data == drf.data
        assert aiodrf.data == {
            "data": {"title": "Draft"},
            "files": {"upload": ["notes.txt", "hello"]},
        }

    @urls
    async def test_form_encoded(self):
        response = await self.both(
            data={"name": "Ursula"}, format=None, content_type=None
        )
        assert response.status_code in (200, 415)

    @urls
    async def test_malformed_json(self):
        response = await self.both(data="{not json", content_type="application/json")
        assert response.status_code == 400
        assert "JSON parse error" in response.data["detail"]

    @urls
    async def test_unsupported_media_type(self):
        response = await self.both(data="<a/>", content_type="application/xml")
        assert response.status_code == 415

    @urls
    async def test_empty_body(self):
        response = await self.both(data="", content_type="application/json")
        assert response.data == {"data": {}, "files": {}}

    @urls
    @override_settings(DATA_UPLOAD_MAX_MEMORY_SIZE=64)
    async def test_form_data_above_djangos_limit(self):
        # DRF 3.17+ lets Django refuse the body (400, no DRF response);
        # DRF 3.16 reads it regardless. aiodrf does what the installed DRF does.
        body = "name=" + "x" * 1024
        form = "application/x-www-form-urlencoded"
        drf = await self.api("post", "/drf/echo/", data=body, content_type=form)
        aiodrf = await self.api("post", "/aiodrf/echo/", data=body, content_type=form)
        assert aiodrf.status_code == drf.status_code
        assert getattr(aiodrf, "data", None) == getattr(drf, "data", None)
        if DRF_VERSION >= (3, 17):
            assert aiodrf.status_code == 400


@urls
class BodyHopTests(TestCase):
    async def test_a_body_in_memory_is_parsed_on_the_event_loop(self):
        with count_hops() as hops:
            await AsyncAPIClient().post("/aiodrf/echo/", data={"name": "Ursula"})
        assert hops.calls == []

    @override_settings(FILE_UPLOAD_MAX_MEMORY_SIZE=1024)
    async def test_a_spooled_body_is_read_in_a_thread(self):
        with count_hops() as hops:
            await AsyncAPIClient().post("/aiodrf/echo/", data={"text": "x" * 4096})
        assert hops.calls == ["Request._load_data_and_files"]

    async def test_multipart_is_parsed_in_a_thread(self):
        upload = SimpleUploadedFile("notes.txt", b"hello")
        with count_hops() as hops:
            await AsyncAPIClient().post(
                "/aiodrf/echo/", data={"upload": upload}, format="multipart"
            )
        assert hops.calls == ["Request._load_data_and_files"]


# -- Translations and time zones across thread hops -------------------------------------


class MomentSerializer(drf_serializers.Serializer):
    at = drf_serializers.DateTimeField()


MOMENT = datetime.datetime(2026, 1, 5, 12, 0, tzinfo=datetime.UTC)


class ContextPolicies:
    authentication_classes = []
    permission_classes = [AllowAny]
    queryset = Author.objects.all()
    serializer_class = AuthorSerializer


class DRFAuthors(ContextPolicies, drf_viewsets.ModelViewSet):
    def list(self, request, *args, **kwargs):
        return DRFResponse(MomentSerializer({"at": MOMENT}).data)


class Authors(ContextPolicies, viewsets.ModelViewSet):
    async def list(self, request, *args, **kwargs):
        # ``run_sync``: the representation happens in a worker thread.
        with override_settings(AIODRF={"REPRESENTATION_MODE": "thread"}, FASTDRF={}):
            return Response(await aio.data(MomentSerializer({"at": MOMENT})))


def istanbul_middleware(get_response):
    """What Django's documentation suggests for per-user time zones."""
    zone = zoneinfo.ZoneInfo("Europe/Istanbul")
    if iscoroutinefunction(get_response):

        async def middleware(request):
            timezone.activate(zone)
            try:
                return await get_response(request)
            finally:
                timezone.deactivate()

        return middleware

    def middleware(request):
        timezone.activate(zone)
        try:
            return get_response(request)
        finally:
            timezone.deactivate()

    return middleware


istanbul_middleware.sync_capable = istanbul_middleware.async_capable = True


@both_transports
class _ContextTests:
    @urls
    @override_settings(MIDDLEWARE=["django.middleware.locale.LocaleMiddleware"])
    async def test_validation_messages_are_translated_in_the_worker_thread(self):
        # LocaleMiddleware leaves its language active; the next request of a
        # server activates its own, the next test would inherit this one.
        self.addCleanup(translation.deactivate)
        english = await self.api("post", "/aiodrf/authors/", data={})
        drf = await self.api(
            "post", "/drf/authors/", data={}, HTTP_ACCEPT_LANGUAGE="tr"
        )
        aiodrf = await self.api(
            "post", "/aiodrf/authors/", data={}, HTTP_ACCEPT_LANGUAGE="tr"
        )
        assert aiodrf.status_code == 400
        assert aiodrf.data == drf.data
        assert aiodrf.data != english.data

    @urls
    @override_settings(MIDDLEWARE=[f"{__name__}.istanbul_middleware"])
    async def test_the_active_time_zone_follows_the_request(self):
        drf = await self.api("get", "/drf/authors/")
        aiodrf = await self.api("get", "/aiodrf/authors/")
        assert aiodrf.data == drf.data == {"at": "2026-01-05T15:00:00+03:00"}


# -- Synchronous middleware ---------------------------------------------------------------


def sync_only_middleware(get_response):
    # Django adapts what follows: the async view runs through
    # ``async_to_sync`` in the thread of this middleware.
    def middleware(request):
        response = get_response(request)
        response["X-Sync-Middleware"] = "1"
        return response

    return middleware


sync_only_middleware.sync_capable = True
sync_only_middleware.async_capable = False


@both_transports
class _SyncMiddlewareTests:
    @urls
    @override_settings(MIDDLEWARE=[f"{__name__}.sync_only_middleware"])
    async def test_aiodrf_views_work_behind_synchronous_middleware(self):
        created = await self.api("post", "/aiodrf/authors/", data={"name": "Ursula"})
        assert created.status_code == 201, created.data
        assert created["X-Sync-Middleware"] == "1"
        fetched = await self.api("get", f"/aiodrf/authors/{created.data['id']}/")
        assert fetched.data == {"id": created.data["id"], "name": "Ursula"}


# -- Streaming ---------------------------------------------------------------------------------


class Stream(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    async def get(self, request):
        async def lines():
            async for author in Author.objects.order_by("pk"):
                yield json.dumps({"name": author.name}) + "\n"

        return StreamingHttpResponse(lines(), content_type="application/x-ndjson")


@urls
class StreamingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Author.objects.create(name="Ursula")
        Author.objects.create(name="Octavia")

    async def test_an_async_iterator_is_streamed(self):
        response = await AsyncAPIClient().get("/aiodrf/stream/")
        assert response.status_code == 200
        chunks = [chunk async for chunk in response.streaming_content]
        assert b"".join(chunks) == b'{"name": "Ursula"}\n{"name": "Octavia"}\n'


# -- ATOMIC_REQUESTS -------------------------------------------------------------------------


@urls
class AtomicRequestsTests(TestCase):
    async def test_django_refuses_async_views(self):
        # Hence ``ATOMIC_SAVE``, and the system check ``aiodrf.W002``.
        with (
            mock.patch.dict(connections.settings["default"], {"ATOMIC_REQUESTS": True}),
            pytest.raises(RuntimeError, match="ATOMIC_REQUESTS"),
        ):
            await AsyncAPIClient().get("/aiodrf/authors/")


# -- Database routers ---------------------------------------------------------------------


class AuthorsElsewhere:
    def db_for_read(self, model, **hints):
        return "other" if model is Author else None

    db_for_write = db_for_read

    def allow_migrate(self, db, app_label, **hints):
        return True


class HalfSavedAuthorSerializer(AuthorSerializer):
    def create(self, validated_data):
        super().create(validated_data)
        raise RuntimeError("after the insert")


class FailingAuthors(ContextPolicies, viewsets.ModelViewSet):
    serializer_class = HalfSavedAuthorSerializer


class _RouterTests:
    databases = {"default", "other"}

    @urls
    @override_settings(DATABASE_ROUTERS=[f"{__name__}.AuthorsElsewhere"])
    async def test_writes_go_where_the_router_sends_them(self):
        created = await self.api("post", "/aiodrf/authors/", data={"name": "Ursula"})
        assert created.status_code == 201, created.data
        assert [a.name async for a in Author.objects.using("other").all()] == ["Ursula"]
        assert not await Author.objects.using("default").aexists()
        listed = await self.api("get", "/aiodrf/authors/")
        assert listed.status_code == 200

    @urls
    @override_settings(DATABASE_ROUTERS=[f"{__name__}.AuthorsElsewhere"])
    async def test_atomic_save_rolls_back_on_the_routed_database(self):
        # ``ATOMIC_SAVE`` has to open its transaction where the write goes.
        with pytest.raises(RuntimeError, match="after the insert"):
            await self.api("post", "/aiodrf/failing/", data={"name": "Ursula"})
        assert not await Author.objects.using("other").aexists()
        with (
            override_settings(AIODRF={"ATOMIC_SAVE": False}, FASTDRF={}),
            pytest.raises(RuntimeError),
        ):
            await self.api("post", "/aiodrf/failing/", data={"name": "Ursula"})
        assert await Author.objects.using("other").aexists()


both_transports(_RouterTests)


class ProjectRouter(AuthorsElsewhere):
    # A router of the project's may query (a tenant lookup, say).
    db_for_write = async_unsafe("router on the loop")(AuthorsElsewhere.db_for_read)


class AtomicSaveRouting(TestCase):
    databases = {"default", "other"}

    @override_settings(DATABASE_ROUTERS=[f"{__name__}.ProjectRouter"])
    async def test_asave_asks_the_routers_in_the_worker(self):
        serializer = AuthorSerializer(data={"name": "Ursula"})
        assert await aio.is_valid(serializer), serializer.errors
        await aio.save(serializer)
        assert await Author.objects.using("other").filter(name="Ursula").aexists()

    async def test_a_list_update_is_atomic_where_its_instances_are_saved(self):
        # ``Model.save()`` writes where each instance came from.
        class RenameAll(drf_serializers.ListSerializer):
            def update(self, instance, validated_data):
                for author, attrs in zip(instance, validated_data, strict=True):
                    author.name = attrs["name"]
                    author.save()
                    if author.name == "fails":
                        raise RuntimeError("after the first save")
                return instance

        await Author.objects.using("other").abulk_create(
            [Author(name="a"), Author(name="b")]
        )
        authors = [a async for a in Author.objects.using("other").order_by("pk")]
        serializer = RenameAll(
            authors,
            child=AuthorSerializer(),
            data=[{"name": "renamed"}, {"name": "fails"}],
        )
        assert await aio.is_valid(serializer), serializer.errors
        with pytest.raises(RuntimeError, match="after the first save"):
            await aio.save(serializer)
        names = [a.name async for a in Author.objects.using("other").order_by("pk")]
        assert names == ["a", "b"]


urlpatterns = [
    path("drf/account/", DRFAccount.as_view()),
    path("aiodrf/account/", Account.as_view()),
    path("drf/echo/", DRFEcho.as_view()),
    path("aiodrf/echo/", Echo.as_view()),
    path("drf/authors/", DRFAuthors.as_view({"get": "list", "post": "create"})),
    path("aiodrf/authors/", Authors.as_view({"get": "list", "post": "create"})),
    path("aiodrf/authors/<int:pk>/", Authors.as_view({"get": "retrieve"})),
    path("aiodrf/failing/", FailingAuthors.as_view({"post": "create"})),
    path("aiodrf/stream/", Stream.as_view()),
]


class BrokenParser(JSONParser):
    # A parser of the project's: parsed in the worker.
    def parse(self, stream, media_type=None, parser_context=None):
        raise AttributeError("broken parser")


async def test_an_attribute_error_of_a_parser_is_wrapped_as_drf_wraps_it():
    # Else ``hasattr(request, "data")`` would hide the failure.
    from rest_framework.request import WrappedAttributeError

    from aiodrf.request import Request
    from aiodrf.test import AsyncAPIRequestFactory

    django_request = AsyncAPIRequestFactory().post("/", {"a": 1}, format="json")
    request = Request(django_request, parsers=[BrokenParser()])
    with pytest.raises(WrappedAttributeError, match="broken parser"):
        await request.adata()
