"""What the ecosystem tests share: the same view written for DRF and for aiodrf."""

from django.contrib.auth.models import User
from rest_framework import views as drf_views
from rest_framework.response import Response as DRFResponse

from aiodrf.response import Response
from aiodrf.views import APIView


class DRFWhoAmI(drf_views.APIView):
    def get(self, request):
        return DRFResponse({"user": request.user.get_username()})


class WhoAmI(APIView):
    async def get(self, request):
        return Response({"user": request.user.get_username()})


def whoami_views(**attrs):
    """
    ``(drf_view, aiodrf_view)`` answering with the authenticated user's name;
    ``attrs`` become class attributes of both (policies, ``required_scopes``).
    """
    return tuple(
        type(base.__name__, (base,), {"__module__": __name__, **attrs}).as_view()
        for base in (DRFWhoAmI, WhoAmI)
    )


class UserFixture:
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("ursula", password="earthsea")


def same_response(first, second):
    """Status, body and the headers a client acts on."""
    headers = ("WWW-Authenticate", "Retry-After", "Allow", "Vary")
    return (
        first.status_code == second.status_code
        and first.data == second.data
        and all(first.headers.get(name) == second.headers.get(name) for name in headers)
    )
