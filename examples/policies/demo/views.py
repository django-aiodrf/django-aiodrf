"""Authentication, permissions, throttling and conditional request examples."""

from django.urls import path
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny, IsAuthenticated

from aiodrf import aio, authentication, serializers
from aiodrf.cache import cache_page
from aiodrf.permissions import BasePermission
from aiodrf.response import Response
from aiodrf.throttling import AnonFixedWindowRateThrottle
from aiodrf.views import APIView


class Public(APIView):
    @extend_schema(exclude=True)
    async def get(self, request):
        return Response({"public": True})


class Challenge(authentication.TokenAuthentication):
    async def aauthenticate_header(self, request):
        return "Token"


class Allowed(BasePermission):
    async def ahas_permission(self, request, view):
        return True


class Limit(AnonFixedWindowRateThrottle):
    rate = "20/min"


class Private(APIView):
    authentication_classes = [
        Challenge,
        authentication.BasicAuthentication,
        authentication.SessionAuthentication,
    ]
    permission_classes = [IsAuthenticated & Allowed]
    throttle_classes = [Limit]

    @extend_schema(exclude=True)
    async def get(self, request):
        user = await request.auser()
        return Response({"username": user.get_username()})

    @extend_schema(exclude=True)
    async def post(self, request):
        return Response({"accepted": True})


class Conditional(Public):
    permission_classes = [AllowAny]

    async def aget_etag(self, request, *args, **kwargs):
        return "demo-v1"


class SearchInput(serializers.Serializer):
    term = serializers.CharField(max_length=40)


class SearchResult(serializers.Serializer):
    matches = serializers.ListField(child=serializers.CharField())


class Search(APIView):
    async def query(self, request):
        serializer = SearchInput(data=request.data)
        await aio.is_valid(serializer, raise_exception=True)
        return Response({"matches": [serializer.validated_data["term"]]})

    @extend_schema(request=SearchInput, responses=SearchResult)
    async def post(self, request):
        # OpenAPI 3.0/3.1 cannot describe QUERY; offer an explicit POST endpoint
        # for generated clients. POST retains its own CSRF/cache semantics.
        return await self.query(request)


urlpatterns = [
    path("public/", cache_page(30)(Public.as_view())),
    path("private/", Private.as_view()),
    path("conditional/", Conditional.as_view()),
    path("search/", Search.as_view()),
]
