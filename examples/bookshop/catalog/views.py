"""Bookshop CRUD, filtering, stock enrichment and streaming endpoints."""

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny

from aiodrf import viewsets
from aiodrf.contrib.spectacular import StreamSchema
from aiodrf.response import Response, StreamingResponse
from aiodrf.views import APIView

from .models import Book
from .serializers import BookSerializer, ExportRow, SearchParameters, SearchResults


class BookViewSet(viewsets.ModelViewSet):
    queryset = Book.objects.order_by("id")
    serializer_class = BookSerializer
    filterset_fields = ["author__name"]

    async def aget_etag(self, request, *args, **kwargs):
        # Answers If-None-Match with 304 and If-Match with 412 before the
        # handler runs, after authentication and permissions. The lookup is
        # the retrieve's own: its queryset, a 404 for an unknown or malformed
        # id, and the object permissions.
        if self.action != "retrieve" and request.method not in ("PUT", "PATCH"):
            return None
        book = await self.aget_object()
        return book.updated.isoformat()

    @extend_schema(
        responses={
            (200, "application/x-ndjson"): OpenApiResponse(
                StreamSchema(ExportRow), description="One book per line."
            )
        }
    )
    @action(detail=False, pagination_class=None, filter_backends=[])
    async def export(self, request):
        """Every book as newline-delimited JSON, read in chunks."""

        async def rows():
            async for book in (
                Book.objects.select_related("author")
                .order_by("id")
                .aiterator(chunk_size=100)
            ):
                # The author is loaded: DRF's representation does no I/O here.
                yield ExportRow(book).data

        return StreamingResponse(rows())


class BookSearch(APIView):
    permission_classes = [AllowAny]
    query_serializer_class = SearchParameters

    @extend_schema(responses=SearchResults)
    async def get(self, request):
        # The parameters are documented by aiodrf.contrib.spectacular.AutoSchema;
        # a 400 in DRF's error format when the query string is invalid.
        params = await self.aget_validated_query_params()
        books = Book.objects.filter(title__icontains=params["q"]).order_by("title")
        titles = books.values_list("title", flat=True)[: params["limit"]]
        return Response({"titles": [title async for title in titles]})
