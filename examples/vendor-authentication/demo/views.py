"""Vendor authentication classes and opt-in credential checks."""

from auth_kit.authentication import JWTCookieAuthentication
from django.urls import path
from knox.auth import TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.views import TokenObtainPairView

from aiodrf.response import Response
from aiodrf.views import APIView


class Identity(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    async def get(self, request):
        user = await request.auser()
        return Response({"username": user.get_username()})


urlpatterns = [
    path("token/", TokenObtainPairView.as_view()),
    path("jwt/", Identity.as_view()),
    path("knox/", Identity.as_view(authentication_classes=[TokenAuthentication])),
    path("cookie/", Identity.as_view(authentication_classes=[JWTCookieAuthentication])),
]
