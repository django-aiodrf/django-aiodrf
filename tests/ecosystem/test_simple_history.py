"""
django-simple-history records who changed a row from the request its
middleware keeps in a context variable. aiodrf saves in a worker thread, so
the variable has to follow the request there.
"""

import asyncio

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import include, path
from rest_framework import serializers
from rest_framework.routers import SimpleRouter

from aiodrf import viewsets
from aiodrf.test import AsyncAPIClient
from tests.base import both_transports
from tests.ecosystem.base import UserFixture
from tests.ecosystem.models import Document


class DocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Document
        fields = ["id", "title"]


class Documents(viewsets.ModelViewSet):
    queryset = Document.objects.all()
    serializer_class = DocumentSerializer


router = SimpleRouter()
router.register("documents", Documents)
urlpatterns = [path("", include(router.urls))]


@both_transports
class _HistoryTests(UserFixture):
    @override_settings(
        ROOT_URLCONF=__name__,
        MIDDLEWARE=[
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
            "simple_history.middleware.HistoryRequestMiddleware",
        ],
    )
    async def test_changes_are_attributed_to_the_authenticated_user(self):
        self.client.force_authenticate(self.user)
        created = await self.api("post", "/documents/", data={"title": "Draft"})
        assert created.status_code == 201, created.data
        url = f"/documents/{created.data['id']}/"
        assert (
            await self.api("patch", url, data={"title": "Final"})
        ).status_code == 200
        assert (await self.api("delete", url)).status_code == 204

        history = [
            (record.history_type, record.title, record.history_user_id)
            async for record in Document.history.order_by("history_id")
        ]
        assert history == [
            ("+", "Draft", self.user.pk),
            ("~", "Final", self.user.pk),
            ("-", "Final", self.user.pk),
        ]


@override_settings(
    ROOT_URLCONF=__name__,
    MIDDLEWARE=[
        "django.contrib.sessions.middleware.SessionMiddleware",
        "django.contrib.auth.middleware.AuthenticationMiddleware",
        "simple_history.middleware.HistoryRequestMiddleware",
    ],
)
class SessionHistoryTests(UserFixture, TestCase):
    # A real session login, not force_authenticate(), and two users at once:
    # each change is attributed to the user of its own request.
    async def test_concurrent_users_are_each_recorded(self):
        other = await User.objects.acreate_user("octavia", password="kindred")
        clients = {}
        for user in (self.user, other):
            client = AsyncAPIClient()
            await client.aforce_login(user)
            clients[user.username] = client

        async def edit(username):
            client = clients[username]
            created = await client.post(
                "/documents/", {"title": username}, format="json"
            )
            assert created.status_code == 201, created.data
            url = f"/documents/{created.data['id']}/"
            patched = await client.patch(url, {"title": f"{username}!"}, format="json")
            assert patched.status_code == 200, patched.data

        await asyncio.gather(*(edit(username) for username in clients))
        history = {
            (record.history_type, record.title, record.history_user_id)
            async for record in Document.history.all()
        }
        assert history == {
            ("+", "ursula", self.user.pk),
            ("~", "ursula!", self.user.pk),
            ("+", "octavia", other.pk),
            ("~", "octavia!", other.pk),
        }
