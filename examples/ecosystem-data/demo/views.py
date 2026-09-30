"""Use ordinary synchronous vendor serializers in async generic views."""

from nested_multipart_parser.drf import DrfNestedParser
from rest_filters import FilterBackend

from aiodrf import aio
from aiodrf.response import Response
from aiodrf.views import APIView
from aiodrf.viewsets import ModelViewSet, ReadOnlyModelViewSet

from .filters import BookFilters
from .models import ArchivedNote, Author, Book, Photo, Project
from .serializers import (
    AddressSerializer,
    AuthorSerializer,
    BookSerializer,
    ExpandedBookSerializer,
    NestedBookSerializer,
    NoteSerializer,
    PhotoSerializer,
    ProjectTypes,
    SubmissionSerializer,
)


class Authors(ModelViewSet):
    queryset = Author.objects.order_by("pk")
    serializer_class = AuthorSerializer


class Books(ModelViewSet):
    queryset = (
        Book.objects.select_related("author").prefetch_related("tags").order_by("pk")
    )
    serializer_class = BookSerializer


class NestedBooks(ModelViewSet):
    queryset = Book.objects.select_related("author").order_by("pk")
    serializer_class = NestedBookSerializer


class FilteredBooks(ReadOnlyModelViewSet):
    queryset = Books.queryset
    serializer_class = BookSerializer
    filter_backends = [FilterBackend]
    filterset_class = BookFilters


class ExpandedBooks(ReadOnlyModelViewSet):
    queryset = Book.objects.select_related("author").order_by("pk")
    serializer_class = ExpandedBookSerializer


class AuthorBooks(Books):
    def get_queryset(self):
        return super().get_queryset().filter(author_id=self.kwargs["author_pk"])

    def perform_create(self, serializer):
        # The URL owns the parent relation; a payload cannot move it elsewhere.
        from django.shortcuts import get_object_or_404

        serializer.save(author=get_object_or_404(Author, pk=self.kwargs["author_pk"]))


class Photos(ModelViewSet):
    queryset = Photo.objects.select_related("author").order_by("pk")
    serializer_class = PhotoSerializer


class Notes(ModelViewSet):
    queryset = ArchivedNote.objects.order_by("pk")
    serializer_class = NoteSerializer


class Projects(ModelViewSet):
    queryset = Project.objects.order_by("pk")
    serializer_class = ProjectTypes


class AddressEcho(APIView):
    async def post(self, request):
        serializer = AddressSerializer(data=await request.adata())
        await aio.is_valid(serializer, raise_exception=True)
        await aio.save(serializer)
        return Response(await aio.data(serializer))


class MultipartSummary(APIView):
    parser_classes = [DrfNestedParser]

    async def post(self, request):
        serializer = SubmissionSerializer(data=await request.adata())
        await aio.is_valid(serializer, raise_exception=True)
        values = serializer.validated_data
        # Only metadata is accessed here; reading or persisting the file belongs in a worker.
        return Response(
            {"author": values["author"], "filename": values["attachment"].name}
        )
