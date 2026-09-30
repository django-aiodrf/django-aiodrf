"""
An adrf project, unchanged: its adrf imports are served by
``aiodrf.contrib.adrf_compat``. ``calls`` records which of adrf's hooks ran.
"""

from adrf import generics, serializers, viewsets
from adrf.decorators import api_view
from adrf.fields import CharField
from adrf.generics import aget_object_or_404
from adrf.permissions import AsyncBasePermission
from adrf.requests import AsyncRequest
from adrf.routers import DefaultRouter
from adrf.views import APIView
from django.urls import include, path
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.throttling import BaseThrottle

from tests.testapp.models import Author

calls = []


class Shouting(CharField):
    async def ato_representation(self, value):
        return value.upper()


class AuthorSerializer(serializers.ModelSerializer):
    shout = Shouting(source="name", read_only=True)
    initial = serializers.SerializerMethodField()

    class Meta:
        model = Author
        fields = ["id", "name", "shout", "initial"]

    async def get_initial(self, obj):
        return obj.name[:1]


class NotBanned(AsyncBasePermission):
    message = "banned"

    async def has_permission(self, request, view):
        return request.headers.get("X-Ban") != "1"


class Pages(PageNumberPagination):
    page_size = 2


class AuthorViewSet(viewsets.ModelViewSet):
    queryset = Author.objects.order_by("pk")
    serializer_class = AuthorSerializer
    authentication_classes = []
    permission_classes = [NotBanned]
    pagination_class = Pages

    async def alist(self, request, *args, **kwargs):
        calls.append(("alist", self.action))
        return await super().alist(request, *args, **kwargs)

    async def perform_acreate(self, serializer):
        calls.append("perform_acreate")
        await serializer.asave()

    async def get_apaginated_response(self, data):
        response = await super().get_apaginated_response(data)
        response["X-Paginated"] = "adrf"
        return response

    async def check_async_permissions(self, request, permissions):
        calls.append(
            ("check_async_permissions", [type(p).__name__ for p in permissions])
        )
        await super().check_async_permissions(request, permissions)


class AuthorDetail(generics.RetrieveUpdateDestroyAPIView):
    queryset = Author.objects.all()
    serializer_class = AuthorSerializer
    authentication_classes = []
    permission_classes = []

    async def aupdate(self, request, *args, **kwargs):
        calls.append(("aupdate", kwargs.get("partial", False)))
        return await super().aupdate(request, *args, **kwargs)


class Echo(APIView):
    authentication_classes = []
    permission_classes = []

    async def get(self, request):
        return Response({"async_request": isinstance(request, AsyncRequest)})


@api_view(["GET"])
async def lookup(request, pk):
    author = await aget_object_or_404(Author.objects.all(), pk=pk)
    return Response(await AuthorSerializer(author).adata)


class Gate(BaseThrottle):
    async def allow_request(self, request, view):
        return request.headers.get("X-Throttle") != "1"

    def wait(self):
        return 7


class Throttled(APIView):
    authentication_classes = []
    permission_classes = []
    throttle_classes = [Gate]

    async def get(self, request):
        return Response({"ok": True})


router = DefaultRouter()
router.register("authors", AuthorViewSet, basename="author")

urlpatterns = [
    path("", include(router.urls)),
    path("detail/<int:pk>/", AuthorDetail.as_view()),
    path("echo/", Echo.as_view()),
    path("lookup/<str:pk>/", lookup),
    path("throttled/", Throttled.as_view()),
]
