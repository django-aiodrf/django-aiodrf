"""
django-phonenumber-field's serializer field parses numbers with
``phonenumbers`` and reads ``PHONENUMBER_DEFAULT_REGION`` when the field is
constructed, which DRF does again for every serializer instance.
"""

import pytest
from django.test import override_settings
from django.urls import path
from phonenumber_field.phonenumber import PhoneNumber
from phonenumber_field.serializerfields import PhoneNumberField
from rest_framework import serializers
from rest_framework import viewsets as drf_viewsets
from rest_framework.permissions import AllowAny

from aiodrf import viewsets
from aiodrf.test import count_hops
from tests.base import both_transports
from tests.ecosystem.models import PhoneContact

# django-phonenumber-field reads its model field with its own descriptor, so its
# serializers cannot be compiled: the tests run them on DRF's code whatever fallback the
# run's profile sets.
pytestmark = pytest.mark.aiodrf_settings(SERIALIZER_BACKEND_FALLBACK="drf")


class ContactSerializer(serializers.ModelSerializer):
    phone = PhoneNumberField()

    class Meta:
        model = PhoneContact
        fields = ["id", "name", "phone"]


class Policies:
    queryset = PhoneContact.objects.order_by("pk")
    serializer_class = ContactSerializer
    authentication_classes = []
    permission_classes = [AllowAny]


class DRFContacts(Policies, drf_viewsets.ModelViewSet):
    pass


class Contacts(Policies, viewsets.ModelViewSet):
    pass


urlpatterns = [
    path("drf/", DRFContacts.as_view({"get": "list", "post": "create"})),
    path("aiodrf/", Contacts.as_view({"get": "list", "post": "create"})),
]
urls = override_settings(ROOT_URLCONF=__name__)


async def post_both(test, payload):
    drf = await test.api("post", "/drf/", data=payload)
    aiodrf = await test.api("post", "/aiodrf/", data=payload)
    assert aiodrf.status_code == drf.status_code
    if drf.status_code == 201:
        assert aiodrf.data["phone"] == drf.data["phone"]
    else:
        assert aiodrf.data == drf.data
    return aiodrf


@both_transports
class _PhoneNumberTests:
    @urls
    async def test_an_international_number_is_stored_in_e164(self):
        with count_hops() as hops:
            response = await post_both(
                self, {"name": "Ada", "phone": "+44 20 7946 0958"}
            )
        assert response.status_code == 201
        assert response.data["phone"] == "+442079460958"
        stored = await PhoneContact.objects.aget(pk=response.data["id"])
        assert stored.phone == PhoneNumber.from_string("+442079460958")
        if self.transport == "asgi":
            # DRF's view is a plain sync view to Django; aiodrf's create is one hop.
            assert hops.calls == ["CreateModelMixin._create"]

    @urls
    async def test_invalid_numbers_are_drfs_400(self):
        for phone in ("12", "not a number", "+1 555 0000 000000"):
            with self.subTest(phone=phone):
                response = await post_both(self, {"name": "Ada", "phone": phone})
                assert response.status_code == 400
                assert response.data == {"phone": ["Enter a valid phone number."]}

    @urls
    async def test_the_default_region_is_read_for_each_request(self):
        # A national number needs a region; the setting changes between
        # requests and each request's serializer sees the current one.
        national = {"name": "Ada", "phone": "020 7946 0958"}
        response = await post_both(self, national)
        assert response.status_code == 400
        with override_settings(PHONENUMBER_DEFAULT_REGION="GB"):
            response = await post_both(self, national)
            assert response.status_code == 201
            assert response.data["phone"] == "+442079460958"
        with override_settings(PHONENUMBER_DEFAULT_REGION="FR"):
            response = await post_both(self, national)
            assert response.status_code == 400
