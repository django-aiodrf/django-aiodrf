"""
django-axes locks accounts out after failed logins. It listens to the signals
``django.contrib.auth.authenticate()`` sends, which DRF's
``BasicAuthentication`` calls; under aiodrf that happens in a worker thread.
"""

import base64

from django.contrib.auth.models import User
from django.test import override_settings
from django.urls import path
from rest_framework.authentication import BasicAuthentication
from rest_framework.permissions import IsAuthenticated

from tests.base import both_transports
from tests.ecosystem.base import whoami_views

drf_whoami, whoami = whoami_views(
    authentication_classes=[BasicAuthentication], permission_classes=[IsAuthenticated]
)
urlpatterns = [path("drf/", drf_whoami), path("aiodrf/", whoami)]


def basic(username, password):
    return "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()


@both_transports
class _LockoutTests:
    @classmethod
    def setUpTestData(cls):
        for username in ("drf-user", "aiodrf-user"):
            User.objects.create_user(username, password="earthsea")

    @override_settings(
        ROOT_URLCONF=__name__,
        AXES_ENABLED=True,
        AXES_FAILURE_LIMIT=2,
        AXES_LOCKOUT_PARAMETERS=["username"],
        AUTHENTICATION_BACKENDS=[
            "axes.backends.AxesStandaloneBackend",
            "django.contrib.auth.backends.ModelBackend",
        ],
        MIDDLEWARE=["axes.middleware.AxesMiddleware"],
    )
    async def test_aiodrf_locks_out_like_drf(self):
        statuses = {}
        for name in ("drf", "aiodrf"):
            username = f"{name}-user"
            attempts = ["earthsea", "wrong", "wrong", "earthsea"]
            statuses[name] = [
                (
                    await self.api(
                        "get", f"/{name}/", HTTP_AUTHORIZATION=basic(username, password)
                    )
                ).status_code
                for password in attempts
            ]
        assert statuses["aiodrf"] == statuses["drf"]
        # The right password no longer helps once the account is locked.
        assert statuses["aiodrf"][0] == 200
        assert statuses["aiodrf"][-1] != 200
