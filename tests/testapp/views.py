from rest_framework import pagination
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.throttling import UserRateThrottle

from aiodrf import generics, permissions, viewsets
from aiodrf.contrib.django_filters import DjangoFilterBackend
from aiodrf.decorators import action, api_view
from aiodrf.filters import OrderingFilter, SearchFilter
from aiodrf.response import Response
from aiodrf.views import APIView
from tests.testapp.models import Author, Book
from tests.testapp.serializers import (
    AsyncHookBookSerializer,
    AuthorSerializer,
    BookSerializer,
    NestedBookSerializer,
)


class SmallPages(pagination.PageNumberPagination):
    page_size = 2


class BookViewSet(viewsets.ModelViewSet):
    queryset = Book.objects.all()
    serializer_class = BookSerializer
    pagination_class = SmallPages
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["author", "pages"]
    search_fields = ["title"]
    ordering_fields = ["title", "pages"]

    @action(detail=True, methods=["post"])
    async def rename(self, request, pk=None):
        book = await self.aget_object()
        book.title = (await request.adata())["title"]
        await book.asave(update_fields=["title"])
        return Response({"title": book.title})

    @action(detail=False)
    def count(self, request):
        # A sync action: it runs in a thread.
        return Response({"count": self.get_queryset().count()})


class NestedBookViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Book.objects.all()
    serializer_class = NestedBookSerializer


class AsyncHookBookViewSet(viewsets.ModelViewSet):
    queryset = Book.objects.all()
    serializer_class = AsyncHookBookSerializer


class LegacyBookViewSet(viewsets.ModelViewSet):
    """Written for DRF: sync hooks only. Must keep working unchanged."""

    serializer_class = BookSerializer

    def get_queryset(self):
        return Book.objects.filter(pages__gte=0)

    def perform_create(self, serializer):
        serializer.save(
            owner=self.request.user if self.request.user.is_authenticated else None
        )

    def get_object(self):
        obj = super().get_object()
        self.fetched_via_sync_get_object = True
        return obj

    def check_permissions(self, request):
        super().check_permissions(request)
        if request.headers.get("X-Deny"):
            self.permission_denied(request, message="Denied by sync override.")


class AuthorListView(generics.ListCreateAPIView):
    queryset = Author.objects.all()
    serializer_class = AuthorSerializer


class HeaderPermission(permissions.BasePermission):
    message = "Missing X-Allow header."

    async def ahas_permission(self, request, view):
        return request.headers.get("X-Allow") == "1"


class GuardedView(APIView):
    authentication_classes = []
    permission_classes = [HeaderPermission]

    async def get(self, request):
        return Response({"ok": True})


class MixedView(APIView):
    async def get(self, request):
        return Response({"handler": "async"})

    def post(self, request):
        return Response({"handler": "sync", "count": Author.objects.count()})


class WhoAmIView(APIView):
    authentication_classes = [TokenAuthentication, SessionAuthentication]
    permission_classes = [IsAuthenticated]

    async def get(self, request):
        user = await request.auser()
        return Response({"username": user.get_username()})

    async def post(self, request):
        return Response({"username": request.user.get_username()})


class ThrottledView(APIView):
    throttle_classes = [UserRateThrottle]

    async def get(self, request):
        return Response({"ok": True})


@api_view(["GET", "POST"])
async def async_function_view(request):
    if request.method == "POST":
        return Response({"echo": await request.adata()})
    return Response({"authors": await Author.objects.acount()})


@api_view()
def sync_function_view(request):
    return Response({"authors": Author.objects.count()})
