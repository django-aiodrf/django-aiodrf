"""
Django features used through an aiodrf view. Where DRF has an answer, the same
view written for DRF is the control.
"""

import json
import tempfile
import threading
from pathlib import Path

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth import aauthenticate, alogin, alogout
from django.contrib.auth.models import User
from django.core import mail
from django.core.mail import send_mail
from django.core.signals import request_finished
from django.db.models.signals import post_save
from django.http import FileResponse
from django.test import TestCase, override_settings
from django.urls import path
from rest_framework import generics as drf_generics
from rest_framework import serializers
from rest_framework.authentication import BasicAuthentication, SessionAuthentication
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response as DRFResponse
from rest_framework.views import APIView as DRFView

from aiodrf import generics
from aiodrf.compat import DJANGO_VERSION
from aiodrf.response import Response, StreamingResponse
from aiodrf.test import AsyncAPIClient, AsyncAPIRequestFactory, count_hops
from aiodrf.utils import run_sync
from aiodrf.views import APIView
from tests.base import both_transports
from tests.testapp.models import Author, Book, Invoice, Seat, Shipment


class Open:
    authentication_classes = []
    permission_classes = [AllowAny]


async def body(response):
    """The content of a streaming response, whichever iterator it has."""
    if response.is_async:
        return b"".join([chunk async for chunk in response.streaming_content])
    return b"".join(response.streaming_content)


# -- Composite primary keys ----------------------------------------------------------


class ShipmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Shipment
        fields = ["carrier", "number", "note"]


class ShipmentViews:
    serializer_class = ShipmentSerializer
    lookup_field = "number"

    def get_queryset(self):
        return Shipment.objects.filter(carrier=self.kwargs["carrier"]).order_by(
            "number"
        )


class Shipments(Open, ShipmentViews, generics.ListCreateAPIView):
    pass


class ShipmentDetail(Open, ShipmentViews, generics.RetrieveUpdateDestroyAPIView):
    pass


class DRFShipmentDetail(Open, ShipmentViews, drf_generics.RetrieveUpdateDestroyAPIView):
    pass


# -- Values the database computes -----------------------------------------------------


class InvoiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Invoice
        fields = ["id", "net", "tax", "total"]


class Invoices(Open, generics.CreateAPIView):
    serializer_class = InvoiceSerializer
    queryset = Invoice.objects.all()


class DRFInvoices(Open, drf_generics.CreateAPIView):
    serializer_class = InvoiceSerializer
    queryset = Invoice.objects.all()


# -- Conditional unique constraints ---------------------------------------------------


class SeatSerializer(serializers.ModelSerializer):
    class Meta:
        model = Seat
        fields = ["number", "open"]


class Seats(Open, generics.CreateAPIView):
    serializer_class = SeatSerializer
    queryset = Seat.objects.all()


class DRFSeats(Open, drf_generics.CreateAPIView):
    serializer_class = SeatSerializer
    queryset = Seat.objects.all()


# -- Files, mail, authentication ----------------------------------------------------


class Download(Open, APIView):
    opened = []

    async def get(self, request):
        handle = open(request.query_params["path"], "rb")  # noqa: ASYNC230, SIM115 -- FileResponse closes it
        type(self).opened.append(handle)
        return FileResponse(handle, filename="report.txt")


class Notify(Open, APIView):
    async def post(self, request):
        # SMTP is blocking I/O: send from the worker, as any synchronous call.
        sent = await run_sync(send_mail)(
            "Report", "Ready.", "api@example.org", [request.data["to"]]
        )
        return Response({"sent": sent, "thread": threading.get_ident()})


class Login(Open, APIView):
    authentication_classes = [SessionAuthentication]

    async def post(self, request):
        user = await aauthenticate(
            request._request,
            username=request.data["username"],
            password=request.data["password"],
        )
        if user is None:
            return Response({"detail": "Invalid credentials."}, status=400)
        await alogin(request._request, user)
        return Response({"username": user.username})

    async def delete(self, request):
        await alogout(request._request)
        return Response(status=204)


class Me(APIView):
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated]

    async def get(self, request):
        return Response({"username": (await request.auser()).username})


class Stream(Open, APIView):
    async def get(self, request):
        async def rows():
            async for row in (
                Author.objects.order_by("name").values("name").aiterator(chunk_size=2)
            ):
                yield row

        return StreamingResponse(rows())


class Failing(Open, APIView):
    async def get(self, request):
        raise RuntimeError("failed")


class LazyBookSerializer(serializers.ModelSerializer):
    author = serializers.StringRelatedField()

    class Meta:
        model = Book
        fields = ["author"]


class LazyBooks(Open, generics.ListAPIView):
    queryset = Book.objects.all()
    serializer_class = LazyBookSerializer


class NonceView(Open, APIView):
    async def get(self, request):
        from django.middleware.csp import get_nonce

        return Response({"nonce": str(get_nonce(request._request))})


class DRFNonceView(Open, DRFView):
    def get(self, request):
        from django.middleware.csp import get_nonce

        return DRFResponse({"nonce": str(get_nonce(request._request))})


urlpatterns = [
    path("shipments/<str:carrier>/", Shipments.as_view()),
    path("shipments/<str:carrier>/<int:number>/", ShipmentDetail.as_view()),
    path("drf/shipments/<str:carrier>/<int:number>/", DRFShipmentDetail.as_view()),
    path("invoices/", Invoices.as_view()),
    path("drf/invoices/", DRFInvoices.as_view()),
    path("seats/", Seats.as_view()),
    path("drf/seats/", DRFSeats.as_view()),
    path("download/", Download.as_view()),
    path("notify/", Notify.as_view()),
    path("login/", Login.as_view()),
    path("me/", Me.as_view()),
    path("stream/", Stream.as_view()),
    path("failing/", Failing.as_view()),
    path("nonce/", NonceView.as_view()),
    path("drf/nonce/", DRFNonceView.as_view()),
    path("lazy-books/", LazyBooks.as_view()),
]


class _FeatureTests:
    @classmethod
    def setUpClass(cls):
        cls.enterClassContext(override_settings(ROOT_URLCONF=__name__))
        super().setUpClass()

    # Composite primary keys (Django 5.2).

    async def test_composite_primary_keys_through_generic_views(self):
        created = await self.api(
            "post",
            "/shipments/UPS/",
            data={"carrier": "UPS", "number": 7},
            format="json",
        )
        assert created.status_code == 201, created.content
        await Shipment.objects.acreate(carrier="DHL", number=7, note="other carrier")
        listed = await self.api("get", "/shipments/UPS/")
        assert listed.json() == [{"carrier": "UPS", "number": 7, "note": ""}]
        for prefix in ("", "/drf"):
            detail = await self.api("get", f"{prefix}/shipments/UPS/7/")
            assert detail.json() == {"carrier": "UPS", "number": 7, "note": ""}
        updated = await self.api(
            "patch", "/shipments/UPS/7/", data={"note": "fragile"}, format="json"
        )
        assert updated.json()["note"] == "fragile"
        assert (await self.api("delete", "/shipments/UPS/7/")).status_code == 204
        assert await Shipment.objects.filter(pk=("DHL", 7)).aexists()
        assert not await Shipment.objects.filter(pk=("UPS", 7)).aexists()

    # db_default and GeneratedField (Django 5.0).

    async def test_database_computed_values_in_the_created_representation(self):
        for url in ("/invoices/", "/drf/invoices/"):
            # DRF does not read db_default as a default: the field is required.
            missing = await self.api("post", url, data={"net": 10}, format="json")
            assert missing.status_code == 400
            assert missing.json() == {"tax": ["This field is required."]}
            created = await self.api(
                "post", url, data={"net": 10, "tax": 3}, format="json"
            )
            assert created.status_code == 201, created.content
            # The GeneratedField is read back after the save, in its hop.
            assert {key: created.json()[key] for key in ("net", "tax", "total")} == {
                "net": 10,
                "tax": 3,
                "total": 13,
            }

    # UniqueConstraint(condition=...) (DRF's unique-constraint validators).

    async def test_conditional_unique_constraints_validate_like_drf(self):
        results = {}
        for url in ("/seats/", "/drf/seats/"):
            await Seat.objects.all().adelete()
            results[url] = [
                (response.status_code, response.json())
                for response in [
                    await self.api("post", url, data=data, format="json")
                    for data in (
                        {"number": 1},
                        {"number": 1},
                        {"number": 1, "open": False},
                    )
                ]
            ]
        # aiodrf validates exactly like the installed DRF. Up to DRF 3.18.1
        # the validator ignored the incoming values of the condition's fields
        # and refused the closed seat too; DRF's main branch accepts it
        # (encode/django-rest-framework#10021, found by ``nox -s canary``).
        # DRF's main branch still reports 3.18.1, so the third status is
        # whatever the installed DRF answers; parity is what is asserted.
        assert results["/seats/"] == results["/drf/seats/"]
        assert [status for status, _ in results["/seats/"][:2]] == [201, 400]

    # FileResponse from an async handler.

    async def test_file_responses_are_served_and_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "report.txt"
            report.write_bytes(b"x" * 100_000)
            Download.opened.clear()
            response = await self.api("get", "/download/", data={"path": str(report)})
            assert response.status_code == 200
            assert await body(response) == b"x" * 100_000
            assert 'filename="report.txt"' in response["Content-Disposition"]
            response.close()
            assert Download.opened
            assert all(handle.closed for handle in Download.opened)

    # Mail from an async handler.

    async def test_mail_is_sent_from_the_worker(self):
        mail.outbox.clear()
        response = await self.api(
            "post", "/notify/", data={"to": "ops@example.org"}, format="json"
        )
        assert response.json()["sent"] == 1
        assert [message.to for message in mail.outbox] == [["ops@example.org"]]

    # Session login and logout written with Django's async auth functions.

    async def test_async_login_and_logout_in_a_handler(self):
        await sync_to_async(User.objects.create_user)("ada", password="secret")
        denied = await self.api("get", "/me/")
        assert denied.status_code == 403
        login = await self.api(
            "post",
            "/login/",
            data={"username": "ada", "password": "secret"},
            format="json",
        )
        assert login.json() == {"username": "ada"}
        assert (await self.api("get", "/me/")).json() == {"username": "ada"}
        assert (await self.api("delete", "/login/")).status_code == 204
        assert (await self.api("get", "/me/")).status_code == 403

    # QuerySet.aiterator() as the source of a stream.

    async def test_a_queryset_streamed_with_aiterator(self):
        await Author.objects.abulk_create([Author(name=name) for name in "cab"])
        response = await self.api("get", "/stream/")
        assert [json.loads(line) for line in (await body(response)).splitlines()] == [
            {"name": "a"},
            {"name": "b"},
            {"name": "c"},
        ]


both_transports(_FeatureTests)


def plain_django_view(request):
    from django.http import HttpResponse

    return HttpResponse("plain")


urlpatterns.append(path("plain/", plain_django_view))


@override_settings(ROOT_URLCONF=__name__)
class LoginRequiredTests(TestCase):
    """
    Django 5.1's LoginRequiredMiddleware: DRF's ``as_view()`` marks its views
    ``login_required = False`` (they authenticate themselves), and aiodrf's
    views inherit that.
    """

    middleware = override_settings(
        MIDDLEWARE=[
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
            "django.contrib.auth.middleware.LoginRequiredMiddleware",
        ]
    )

    async def test_drf_authentication_decides_for_aiodrf_views(self):
        await sync_to_async(User.objects.create_user)("ada", password="secret")
        with self.middleware:
            client = AsyncAPIClient()
            # A plain Django view is redirected to the login page.
            assert (await client.get("/plain/")).status_code == 302
            # The aiodrf view answers with DRF's authentication (403: the first
            # authenticator, SessionAuthentication, sends no WWW-Authenticate).
            assert (await client.get("/me/")).status_code == 403
            client.credentials(
                HTTP_AUTHORIZATION="Basic YWRhOnNlY3JldA=="
            )  # ada:secret
            response = await client.get("/me/")
            assert response.status_code == 200
            assert response.json() == {"username": "ada"}
        assert Me.as_view().login_required is False


@override_settings(ROOT_URLCONF=__name__)
class RequestFinishedTests(TestCase):
    """request_finished is sent once, after the response, whatever it was."""

    async def finished_after(self, url):
        finished = []

        def receiver(**kwargs):
            finished.append(True)

        request_finished.connect(receiver)
        try:
            response = await AsyncAPIClient(raise_request_exception=False).get(url)
            if response.streaming:
                [chunk async for chunk in response.streaming_content]
        finally:
            request_finished.disconnect(receiver)
        return response.status_code, len(finished)

    async def test_after_a_stream(self):
        await Author.objects.acreate(name="a")
        assert await self.finished_after("/stream/") == (200, 1)

    async def test_after_an_unhandled_exception(self):
        with self.assertLogs("django.request", "ERROR"):
            assert await self.finished_after("/failing/") == (500, 1)


@pytest.mark.skipif(
    DJANGO_VERSION < (6, 0), reason="ContentSecurityPolicyMiddleware is Django 6.0+"
)
@override_settings(ROOT_URLCONF=__name__)
class ContentSecurityPolicyTests(TestCase):
    async def test_the_nonce_of_the_response_is_the_nonce_of_the_request(self):
        from django.utils.csp import CSP

        with override_settings(
            MIDDLEWARE=["django.middleware.csp.ContentSecurityPolicyMiddleware"],
            SECURE_CSP={"default-src": [CSP.SELF], "script-src": [CSP.SELF, CSP.NONCE]},
        ):
            for url in ("/nonce/", "/drf/nonce/"):
                response = await AsyncAPIClient().get(url)
                nonce = response.json()["nonce"]
                assert f"'nonce-{nonce}'" in response["Content-Security-Policy"], url


@pytest.mark.django_db(transaction=True)
async def test_model_receivers_of_a_generic_create_run_in_its_save_hop():
    seen = []

    def receiver(sender, instance, created, **kwargs):
        # Synchronous ORM work in a receiver, as projects write them.
        seen.append((threading.get_ident(), Author.objects.count()))

    post_save.connect(receiver, sender=Author)
    try:

        class Authors(Open, generics.CreateAPIView):
            queryset = Author.objects.all()

            class serializer_class(serializers.ModelSerializer):
                class Meta:
                    model = Author
                    fields = ["name"]

        with count_hops() as hops:
            response = await Authors.as_view()(
                AsyncAPIRequestFactory().post("/", {"name": "Ada"}, format="json")
            )
    finally:
        post_save.disconnect(receiver, sender=Author)
    assert response.status_code == 201
    assert len(seen) == 1
    assert seen[0][0] != threading.get_ident()
    assert seen[0][1] == 1
    assert hops.calls == ["CreateModelMixin._create"]


@pytest.mark.skipif(DJANGO_VERSION < (6, 1), reason="Fetch modes need Django 6.1")
@override_settings(ROOT_URLCONF=__name__, AIODRF={"FETCH_MODE": "raise"})
class FetchModeResponseTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Book.objects.create(
            title="A", isbn="1", author=Author.objects.create(name="Ada")
        )

    async def test_a_blocked_lazy_load_is_a_500_that_names_the_field(self):
        client = AsyncAPIClient(raise_request_exception=False)
        with self.assertLogs("django.request", "ERROR") as logs:
            response = await client.get("/lazy-books/")
        assert response.status_code == 500
        assert "author" in logs.output[0]
