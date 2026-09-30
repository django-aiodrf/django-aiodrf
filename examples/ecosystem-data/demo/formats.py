"""Route-local representations avoid changing the whole application's wire format."""

from djangorestframework_camel_case.parser import CamelCaseJSONParser
from djangorestframework_camel_case.render import CamelCaseJSONRenderer
from drf_excel.mixins import XLSXFileMixin
from drf_excel.renderers import XLSXRenderer
from drf_orjson_renderer.renderers import ORJSONRenderer
from drf_tweaks.pagination import NoCountsLimitOffsetPagination
from rest_framework import serializers
from rest_framework_datatables.filters import DatatablesFilterBackend
from rest_framework_datatables.pagination import DatatablesPageNumberPagination
from rest_framework_datatables.renderers import DatatablesRenderer
from rest_framework_json_api import serializers as jsonapi_serializers
from rest_framework_json_api.exceptions import (
    exception_handler as jsonapi_exception_handler,
)
from rest_framework_json_api.parsers import JSONParser
from rest_framework_json_api.renderers import JSONRenderer

from aiodrf import aio
from aiodrf.generics import ListAPIView
from aiodrf.response import Response
from aiodrf.views import APIView
from aiodrf.viewsets import ReadOnlyModelViewSet

from .models import Book


class DisplayBookSerializer(serializers.ModelSerializer):
    author_name = serializers.CharField(source="author.name")

    class Meta:
        model = Book
        fields = ["id", "title", "author_name"]


class DisplayBooks(ListAPIView):
    queryset = Book.objects.select_related("author").order_by("pk")
    serializer_class = DisplayBookSerializer


class FastJSONBooks(DisplayBooks):
    renderer_classes = [ORJSONRenderer]


class SpreadsheetBooks(XLSXFileMixin, DisplayBooks):
    renderer_classes = [XLSXRenderer]
    filename = "books.xlsx"


class TableBooks(DisplayBooks):
    renderer_classes = [DatatablesRenderer]
    filter_backends = [DatatablesFilterBackend]
    pagination_class = DatatablesPageNumberPagination


class Pages(NoCountsLimitOffsetPagination):
    default_limit = 2


class UncountedBooks(DisplayBooks):
    pagination_class = Pages


class JSONAPIBookSerializer(jsonapi_serializers.ModelSerializer):
    class Meta:
        model = Book
        fields = ["id", "title"]


class JSONAPIBooks(ReadOnlyModelViewSet):
    queryset = Book.objects.order_by("pk")
    serializer_class = JSONAPIBookSerializer
    renderer_classes = [JSONRenderer]
    parser_classes = [JSONParser]
    resource_name = "books"

    def get_exception_handler(self):
        return jsonapi_exception_handler


class NameSerializer(serializers.Serializer):
    display_name = serializers.CharField(max_length=100)


class CamelCaseEcho(APIView):
    parser_classes = [CamelCaseJSONParser]
    renderer_classes = [CamelCaseJSONRenderer]

    async def post(self, request):
        serializer = NameSerializer(data=await request.adata())
        await aio.is_valid(serializer, raise_exception=True)
        return Response(await aio.data(serializer))
