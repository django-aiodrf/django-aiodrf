from django.urls import include, path
from django_async_backend.db import async_new_connection
from rest_framework import filters, serializers
from rest_framework.permissions import AllowAny, BasePermission, DjangoModelPermissions
from rest_framework.routers import SimpleRouter

from aiodrf.contrib import async_backend as native
from aiodrf.contrib.async_backend import pagination
from aiodrf.contrib.async_backend.filters import DjangoFilterBackend
from aiodrf.response import EventStreamResponse
from tests.async_backend.models import Document, Note, Person
from tests.testapp.models import Author, Book


class Open:
    authentication_classes = []
    permission_classes = [AllowAny]


class BookSerializer(native.ModelSerializer):
    author_name = serializers.CharField(source="author.name", read_only=True)

    class Meta:
        model = Book
        fields = ["id", "title", "isbn", "pages", "author", "author_name", "tags"]
        auto_prefetch = True


class AuthorSerializer(native.ModelSerializer):
    class Meta:
        model = Author
        fields = ["id", "name"]


class PersonSerializer(native.ModelSerializer):
    class Meta:
        model = Person
        fields = ["id", "name", "friends"]


class NoteSerializer(native.ModelSerializer):
    class Meta:
        model = Note
        fields = ["id", "text", "labels"]


class DocumentSerializer(native.ModelSerializer):
    class Meta:
        model = Document
        fields = ["id", "name", "file"]


class Pages(pagination.PageNumberPagination):
    page_size = 2


class Limits(pagination.LimitOffsetPagination):
    default_limit = 2


class Cursors(pagination.CursorPagination):
    page_size = 2
    ordering = "id"


class NotOwner(BasePermission):
    def has_object_permission(self, request, view, obj):
        return obj.title != "private"


class Books(Open, native.ModelViewSet):
    queryset = Book.async_objects.order_by("id")
    serializer_class = BookSerializer
    pagination_class = Pages
    permission_classes = [AllowAny, NotOwner]
    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]
    filterset_fields = ["author"]
    search_fields = ["title"]
    ordering_fields = ["title", "pages"]


class Authors(Open, native.ModelViewSet):
    queryset = Author.async_objects.order_by("id")
    serializer_class = AuthorSerializer
    pagination_class = None


class People(Open, native.ModelViewSet):
    queryset = Person.async_objects.order_by("id")
    serializer_class = PersonSerializer
    pagination_class = None


class Notes(Open, native.ModelViewSet):
    queryset = Note.async_objects.order_by("id")
    serializer_class = NoteSerializer
    pagination_class = None


class Documents(Open, native.ModelViewSet):
    queryset = Document.async_objects.order_by("id")
    serializer_class = DocumentSerializer
    pagination_class = None


class BookEvents(Open, native.RetrieveAPIView):
    queryset = Book.async_objects.all()
    serializer_class = BookSerializer

    async def retrieve(self, request, *args, **kwargs):
        first = await self.aget_object()
        others = Book.async_objects.exclude(pk=first.pk).order_by("id")

        async def events():
            yield {"title": first.title}
            async for book in others:
                yield {"title": book.title}

        async def events_on_a_connection_of_their_own():
            # With keepalive the source runs in a task of its own, and the
            # request's connection belongs to the request's task.
            async with async_new_connection():
                async for event in events():
                    yield event

        if "keepalive" in request.query_params:
            return EventStreamResponse(
                events_on_a_connection_of_their_own(), keepalive=5
            )
        return EventStreamResponse(events())


class PermittedBooks(native.ListCreateAPIView):
    queryset = Book.async_objects.order_by("id")
    serializer_class = BookSerializer
    permission_classes = [DjangoModelPermissions]
    pagination_class = None


class LimitedBooks(Open, native.ListAPIView):
    queryset = Book.async_objects.order_by("id")
    serializer_class = BookSerializer
    pagination_class = Limits


class CursorBooks(Open, native.ListAPIView):
    queryset = Book.async_objects.all()
    serializer_class = BookSerializer
    pagination_class = Cursors


router = SimpleRouter()
router.register("books", Books, basename="book")
router.register("authors", Authors, basename="author")
router.register("people", People, basename="person")
router.register("notes", Notes, basename="note")
router.register("documents", Documents, basename="document")

urlpatterns = [
    path("", include(router.urls)),
    path("limited/", LimitedBooks.as_view()),
    path("cursor/", CursorBooks.as_view()),
    path("events/<int:pk>/", BookEvents.as_view()),
    path("permitted/", PermittedBooks.as_view()),
]
