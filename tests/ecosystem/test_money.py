"""
django-money's serializer field reads the currency from a sibling key of the
input (``price_currency``) in ``get_value`` and builds ``Money`` in
``to_internal_value``; its validators compare ``Money`` objects.
"""

import pytest
from django.test import override_settings
from django.urls import path
from djmoney.contrib.django_rest_framework import MoneyField
from djmoney.money import Money
from rest_framework import serializers
from rest_framework import viewsets as drf_viewsets
from rest_framework.permissions import AllowAny

from aiodrf import viewsets
from aiodrf.test import count_hops
from tests.base import both_transports
from tests.ecosystem.models import MoneyPrice

# django-money's MoneyField is not DRF's, so its serializers cannot be compiled: the
# tests run them on DRF's code whatever fallback the run's profile sets.
pytestmark = pytest.mark.aiodrf_settings(SERIALIZER_BACKEND_FALLBACK="drf")


class PriceSerializer(serializers.ModelSerializer):
    price = MoneyField(max_digits=10, decimal_places=2, min_value=0)

    class Meta:
        model = MoneyPrice
        fields = ["id", "name", "price", "price_currency"]


class Policies:
    queryset = MoneyPrice.objects.order_by("pk")
    serializer_class = PriceSerializer
    authentication_classes = []
    permission_classes = [AllowAny]


class DRFPrices(Policies, drf_viewsets.ModelViewSet):
    pass


class Prices(Policies, viewsets.ModelViewSet):
    pass


urlpatterns = [
    route
    for prefix, viewset in (("drf", DRFPrices), ("aiodrf", Prices))
    for route in (
        path(f"{prefix}/", viewset.as_view({"get": "list", "post": "create"})),
        path(
            f"{prefix}/<int:pk>/",
            viewset.as_view({"get": "retrieve", "patch": "partial_update"}),
        ),
    )
]
urls = override_settings(ROOT_URLCONF=__name__)


def without_id(data):
    return {key: value for key, value in data.items() if key != "id"}


@both_transports
class _MoneyTests:
    @urls
    async def test_amount_and_currency_round_trip(self):
        payload = {"name": "Tea", "price": "3.50", "price_currency": "USD"}
        drf = await self.api("post", "/drf/", data=payload)
        with count_hops() as hops:
            aiodrf = await self.api("post", "/aiodrf/", data=payload)
        assert (aiodrf.status_code, drf.status_code) == (201, 201)
        assert without_id(aiodrf.data) == without_id(drf.data)
        assert without_id(aiodrf.data) == {
            "name": "Tea",
            "price": "3.50",
            "price_currency": "USD",
        }
        stored = await MoneyPrice.objects.aget(pk=aiodrf.data["id"])
        assert stored.price == Money("3.50", "USD")
        if self.transport == "asgi":
            assert hops.calls == ["CreateModelMixin._create"]

        listed = await self.api("get", "/aiodrf/")
        assert [without_id(item) for item in listed.data] == [
            without_id(item) for item in (await self.api("get", "/drf/")).data
        ]

    @urls
    async def test_the_model_default_currency_applies(self):
        payload = {"name": "Tea", "price": "2"}
        drf = await self.api("post", "/drf/", data=payload)
        aiodrf = await self.api("post", "/aiodrf/", data=payload)
        assert without_id(aiodrf.data) == without_id(drf.data)
        assert aiodrf.data["price_currency"] == "EUR"

    @urls
    async def test_validation_errors_are_drfs(self):
        for payload in (
            {"name": "Tea", "price": "-1", "price_currency": "USD"},
            {"name": "Tea", "price": "1", "price_currency": "XXX1"},
            {"name": "Tea", "price": "abc"},
        ):
            with self.subTest(payload=payload):
                drf = await self.api("post", "/drf/", data=payload)
                aiodrf = await self.api("post", "/aiodrf/", data=payload)
                assert aiodrf.status_code == drf.status_code == 400
                assert aiodrf.data == drf.data
        assert await MoneyPrice.objects.acount() == 0

    @urls
    async def test_a_partial_update_of_the_amount_takes_the_default_currency(self):
        # djmoney's ``get_value`` falls back to the model's default currency,
        # not the stored one; the same in DRF.
        drf_price = await MoneyPrice.objects.acreate(name="a", price=Money(1, "GBP"))
        price = await MoneyPrice.objects.acreate(name="b", price=Money(1, "GBP"))
        drf = await self.api("patch", f"/drf/{drf_price.pk}/", data={"price": "5.00"})
        aiodrf = await self.api("patch", f"/aiodrf/{price.pk}/", data={"price": "5.00"})
        assert aiodrf.status_code == drf.status_code == 200
        assert aiodrf.data["price_currency"] == "EUR"
        assert (aiodrf.data["price"], aiodrf.data["price_currency"]) == (
            drf.data["price"],
            drf.data["price_currency"],
        )
