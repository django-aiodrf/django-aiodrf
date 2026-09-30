"""
djangorestframework-dataclasses: ``DataclassSerializer`` validates into a
dataclass instance and saves without the ORM.

Its ``validated_data`` is a dataclass, not a dict, and it overrides
``to_internal_value`` and ``save()``; aiodrf leaves both to the package
(``aiodrf.aio.is_valid``/``save`` in a hand-written handler, the generic
actions in a view).
"""

import dataclasses

import pytest
from django.test import override_settings
from django.urls import path
from rest_framework import generics as drf_generics
from rest_framework import views as drf_views
from rest_framework.permissions import AllowAny
from rest_framework.response import Response as DRFResponse
from rest_framework_dataclasses.serializers import DataclassSerializer

from aiodrf import aio, generics
from aiodrf.response import Response
from aiodrf.test import AsyncAPIClient, count_hops
from aiodrf.views import APIView
from tests.base import both_transports
from tests.ecosystem.base import same_response

# djangorestframework-dataclasses' serializers are not model serializers, so its
# serializers cannot be compiled: the tests run them on DRF's code whatever fallback the
# run's profile sets.
pytestmark = pytest.mark.aiodrf_settings(SERIALIZER_BACKEND_FALLBACK="drf")


@dataclasses.dataclass
class Address:
    city: str
    zip_code: str


@dataclasses.dataclass
class Person:
    name: str
    age: int
    address: Address
    email: str | None = None


SAVED: list[Person] = []


class PersonSerializer(DataclassSerializer):
    class Meta:
        dataclass = Person


class Open:
    authentication_classes = []
    permission_classes = [AllowAny]


class DRFPersonView(Open, drf_views.APIView):
    def post(self, request):
        serializer = PersonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        person = serializer.save()
        return DRFResponse({"type": type(person).__name__, **serializer.data})


class PersonView(Open, APIView):
    async def post(self, request):
        serializer = PersonSerializer(data=await request.adata())
        await aio.is_valid(serializer, raise_exception=True)
        person = await aio.save(serializer)
        return Response({"type": type(person).__name__, **await aio.data(serializer)})


class People(Open):
    serializer_class = PersonSerializer

    def get_queryset(self):
        return list(SAVED)

    def perform_create(self, serializer):
        SAVED.append(serializer.save())


urlpatterns = [
    path("drf/person/", DRFPersonView.as_view()),
    path("aiodrf/person/", PersonView.as_view()),
    path(
        "drf/people/",
        type("People", (People, drf_generics.ListCreateAPIView), {}).as_view(),
    ),
    path(
        "aiodrf/people/",
        type("People", (People, generics.ListCreateAPIView), {}).as_view(),
    ),
]
urls = override_settings(ROOT_URLCONF=__name__)

VALID = {
    "name": "Ursula",
    "age": 88,
    "address": {"city": "Portland", "zip_code": "97201"},
}
INVALID = [
    {},
    {"name": "Ursula", "age": "old", "address": {"city": "Portland"}},
    {"name": "Ursula", "age": 88, "address": "Portland"},
    {**VALID, "email": ["not", "a", "string"]},
]


@both_transports
class _DataclassTests:
    def setUp(self):
        super().setUp()
        SAVED.clear()

    @urls
    async def test_a_hand_written_handler_validates_and_saves_as_in_drf(self):
        for payload in (VALID, *INVALID):
            with self.subTest(payload=payload):
                drf = await self.api("post", "/drf/person/", data=payload)
                aiodrf = await self.api("post", "/aiodrf/person/", data=payload)
                assert same_response(drf, aiodrf), (drf.data, aiodrf.data)
        valid = await self.api("post", "/aiodrf/person/", data=VALID)
        assert valid.data == {"type": "Person", "email": None, **VALID}

    @urls
    async def test_generic_create_and_list_without_the_orm_as_in_drf(self):
        for payload in (VALID, *INVALID):
            with self.subTest(payload=payload):
                drf = await self.api("post", "/drf/people/", data=payload)
                aiodrf = await self.api("post", "/aiodrf/people/", data=payload)
                assert same_response(drf, aiodrf), (drf.data, aiodrf.data)
        assert [type(person) for person in SAVED] == [Person, Person]
        assert (
            SAVED[0]
            == SAVED[1]
            == Person(
                name="Ursula",
                age=88,
                address=Address(city="Portland", zip_code="97201"),
            )
        )
        drf = await self.api("get", "/drf/people/")
        aiodrf = await self.api("get", "/aiodrf/people/")
        assert same_response(drf, aiodrf)
        assert len(aiodrf.data) == 2


@pytest.mark.django_db(transaction=True)
async def test_the_generic_create_costs_one_hop():
    SAVED.clear()
    with count_hops() as hops, override_settings(ROOT_URLCONF=__name__):
        response = await AsyncAPIClient().post("/aiodrf/people/", VALID, format="json")
    assert response.status_code == 201, response.data
    assert hops.count == 1, hops.calls
    assert [Person(**{**VALID, "address": Address(**VALID["address"])})] == SAVED
