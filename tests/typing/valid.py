"""Consumer examples checked by the typecheck session, not executed by pytest."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import assert_type

from django.http import HttpRequest, HttpResponseBase
from rest_framework.request import Request as DRFRequest
from rest_framework.serializers import BaseSerializer
from rest_framework.views import APIView as DRFAPIView

from aiodrf import serializers
from aiodrf.asgi import (
    LifespanApplication,
    LifespanFactory,
    get_asgi_application,
    get_lifespan_state,
)
from aiodrf.permissions import BasePermission
from aiodrf.request import Request
from aiodrf.response import Response, StreamingResponse
from aiodrf.routers import DefaultRouter
from aiodrf.settings import aiodrf_settings
from aiodrf.views import APIView
from aiodrf.viewsets import ModelViewSet
from tests.testapp.models import Author


class Input(serializers.Serializer):
    value = serializers.IntegerField()

    async def avalidate_value(self, value: int) -> int:
        if value < 0:
            raise serializers.ValidationError("Must be nonnegative.")
        return value


class Echo(APIView):
    async def post(self, request: Request) -> Response:
        serializer = Input(data=await request.adata())
        assert_type(await serializer.ais_valid(raise_exception=True), bool)
        return Response(serializer.validated_data)

    async def afinalize_response(
        self,
        request: Request,
        response: HttpResponseBase,
        *args: object,
        **kwargs: object,
    ) -> HttpResponseBase:
        result = await super().afinalize_response(request, response, *args, **kwargs)
        assert_type(result, HttpResponseBase)
        return result


class AsyncPermission(BasePermission):
    async def ahas_permission(self, request: DRFRequest, view: DRFAPIView) -> bool:
        return request.method == "GET"


async def consume() -> None:
    stream = StreamingResponse([{"value": 1}], chunk_size=1)
    async for _ in stream:
        pass
    await stream.aclose()


class AuthorInput(serializers.ModelSerializer[Author]):
    class Meta:
        model = Author
        fields = ["id", "name"]


class Authors(ModelViewSet):
    queryset = Author.objects.all()
    serializer_class = AuthorInput

    async def aperform_create(self, serializer: BaseSerializer) -> None:
        from aiodrf import aio

        await aio.save(serializer)


router = DefaultRouter()
router.register("authors", Authors)
backend: str = aiodrf_settings.SERIALIZER_BACKEND
assert_type(AuthorInput().instance, Author | None)


async def typed_save(serializer: AuthorInput) -> None:
    assert_type(await serializer.asave(), Author)


@dataclass
class Resources:
    name: str


@asynccontextmanager
async def lifespan() -> AsyncGenerator[Resources, None]:
    yield Resources(name="example")


factory: LifespanFactory[Resources] = lifespan
assert_type(get_asgi_application(lifespan=factory), LifespanApplication)


def typed_resources(django_request: HttpRequest, drf_request: Request) -> None:
    assert_type(get_lifespan_state(django_request, Resources), Resources)
    assert_type(get_lifespan_state(drf_request, Resources), Resources)
