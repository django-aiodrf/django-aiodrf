"""Typed request and response schemas with explicit OpenAPI integration."""

import msgspec
import pydantic
from django.urls import path
from drf_spectacular.utils import extend_schema
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from fastdrf.msgspec.parsers import MsgspecJSONParser
from fastdrf.msgspec.renderers import MsgspecJSONRenderer

from aiodrf import aio, generics, serializers
from aiodrf.contrib.msgspec.serializers import MsgspecSerializer
from aiodrf.contrib.pydantic.serializers import PydanticSerializer
from aiodrf.response import Response


class Message(msgspec.Struct):
    name: str
    count: int = 1


class MessageModel(pydantic.BaseModel):
    name: str
    count: int = 1


class MessageOutput(msgspec.Struct):
    name: str
    count: int
    accepted: bool = True


class MessageModelOutput(MessageModel):
    accepted: bool = True


class MsgspecInput(MsgspecSerializer):
    class Meta:
        input_schema = Message
        output_schema = MessageOutput


class PydanticInput(PydanticSerializer):
    class Meta:
        input_schema = MessageModel
        output_schema = MessageModelOutput


class LocalizedMessage(pydantic.BaseModel):
    name: str

    @pydantic.field_validator("name")
    @classmethod
    def normalize(cls, value: str, info: pydantic.ValidationInfo) -> str:
        value = value.strip()
        if not value or len(value) > info.context["name_limit"]:
            raise ValueError("Name must contain 1 to 40 characters")
        return value

    @pydantic.field_serializer("name")
    def label(self, value: str, info: pydantic.FieldSerializationInfo) -> str:
        return info.context["prefix"] + value


class Reference:
    """An application value whose JSON representation is a book reference."""

    def __init__(self, value: str) -> None:
        self.value = value


def decode_reference(target: type, value: object) -> Reference:
    if target is Reference and isinstance(value, str) and value.startswith("book:"):
        return Reference(value)
    raise ValueError("Expected a book reference")


def encode_reference(value: object) -> str:
    if isinstance(value, Reference):
        return value.value
    raise NotImplementedError(type(value).__name__)


def reference_schema(target: type) -> dict:
    if target is Reference:
        return {"type": "string", "pattern": "^book:"}
    raise NotImplementedError(target.__name__)


class BookReference(msgspec.Struct):
    reference: Reference


class ReferenceSerializer(MsgspecSerializer):
    class Meta:
        schema = BookReference
        dec_hook = decode_reference
        enc_hook = encode_reference
        schema_hook = reference_schema


class NativeEcho(generics.GenericAPIView):
    """Validate and represent a native schema without generated DRF fields."""

    async def post(self, request):
        serializer = await self.aget_serializer(data=await request.adata())
        await serializer.ais_valid(raise_exception=True)
        return Response(await serializer.adata())


class ContextEcho(NativeEcho):
    serializer_class = LocalizedMessage

    def get_serializer_context(self):
        return {
            **super().get_serializer_context(),
            "name_limit": 40,
            "prefix": "Hello, ",
        }


class Typed(generics.GenericAPIView):
    serializer_class = MsgspecInput
    parser_classes = [MsgspecJSONParser]
    renderer_classes = [MsgspecJSONRenderer]

    async def post(self, request):
        serializer = await self.aget_serializer(data=request.data)
        await aio.is_valid(serializer, raise_exception=True)
        output = await self.aget_serializer(serializer.validated_data)
        return Response(await aio.data(output))

    async def patch(self, request):
        serializer = await self.aget_serializer(data=request.data, partial=True)
        await aio.is_valid(serializer, raise_exception=True)
        return Response(serializer.validated_data)


class Params(serializers.Serializer):
    limit = serializers.IntegerField(min_value=1, max_value=10, default=2)


class Query(generics.GenericAPIView):
    query_serializer_class = Params
    serializer_class = Params

    @extend_schema(responses=Params)
    async def get(self, request):
        return Response(await self.aget_validated_query_params())


urlpatterns = [
    path("msgspec/", Typed.as_view()),
    path("pydantic/", Typed.as_view(serializer_class=PydanticInput)),
    path("query/", Query.as_view()),
    path("pydantic-context/", ContextEcho.as_view()),
    path("msgspec-custom/", NativeEcho.as_view(serializer_class=ReferenceSerializer)),
    path(
        "root-list/", NativeEcho.as_view(serializer_class=pydantic.RootModel[list[int]])
    ),
    path("schema/", SpectacularAPIView.as_view(), name="schema"),
    path("docs/", SpectacularSwaggerView.as_view(url_name="schema")),
]
