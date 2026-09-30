"""django-idempotency-key's middleware in front of aiodrf's generic views."""

import uuid

import pytest
from django.test import override_settings
from django.urls import path
from rest_framework import serializers
from rest_framework.permissions import AllowAny

from aiodrf import generics
from aiodrf.test import AsyncAPIClient, count_hops
from tests.base import both_transports
from tests.testapp.models import Author


class AuthorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Author
        fields = ["id", "name"]


class Authors(generics.ListCreateAPIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    queryset = Author.objects.all()
    serializer_class = AuthorSerializer


urlpatterns = [path("authors/", Authors.as_view())]
idempotent = override_settings(
    ROOT_URLCONF=__name__,
    MIDDLEWARE=["idempotency_key.middleware.IdempotencyKeyMiddleware"],
)


@both_transports
class _IdempotencyKeyTests:
    @idempotent
    async def test_a_repeated_key_replays_the_first_response_without_a_second_write(
        self,
    ):
        key = str(uuid.uuid4())
        first = await self.api(
            "post",
            "/authors/",
            data={"name": "Ada"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=key,
        )
        # Read now: the in-memory storage keeps this response object and
        # sets the conflict status on it when it replays it.
        status, body = first.status_code, first.json()
        again = await self.api(
            "post",
            "/authors/",
            data={"name": "Ada"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=key,
        )
        assert status == 201
        # The package's default answer to a repeated key: 409 with the stored body.
        assert again.status_code == 409
        assert again.json() == body
        assert await Author.objects.acount() == 1

    @idempotent
    async def test_unsafe_requests_need_a_key_and_safe_ones_do_not(self):
        missing = await self.api(
            "post", "/authors/", data={"name": "Bo"}, format="json"
        )
        assert missing.status_code == 400
        assert (await self.api("get", "/authors/")).status_code == 200
        assert await Author.objects.acount() == 0


@pytest.mark.django_db(transaction=True)
@idempotent
async def test_the_synchronous_middleware_does_not_move_the_view_into_its_thread():
    # Django adapts the middleware; the view stays async and still costs
    # the hops of an ordinary create.
    client = AsyncAPIClient()
    with count_hops() as hops:
        response = await client.post(
            "/authors/",
            {"name": "Ada"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
    assert response.status_code == 201
    assert hops.calls == ["CreateModelMixin._create"]
