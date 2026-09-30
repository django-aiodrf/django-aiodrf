"""
django-auditlog: its middleware puts ``request.user`` in a context variable,
and its signal receivers attribute each change to it. aiodrf saves in a
worker thread, with the request's context.

The middleware reads ``request.user`` before the view runs, so it sees the
user Django's authentication middleware found (a session), not one DRF
authenticates in the view (a JWT, a token): those changes have no actor
unless the view sets it with ``auditlog.context.set_actor``.
"""

import asyncio

from asgiref.sync import sync_to_async
from auditlog.context import set_actor
from auditlog.models import LogEntry
from django.contrib.auth.models import User
from django.db import transaction
from django.test import TestCase, override_settings
from django.urls import path
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.tokens import AccessToken

from aiodrf import viewsets
from aiodrf.test import AsyncAPIClient
from tests.ecosystem.base import UserFixture
from tests.ecosystem.models import AuditedNote


class NoteSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditedNote
        fields = ["id", "title", "secret"]


class Notes(viewsets.ModelViewSet):
    queryset = AuditedNote.objects.all()
    serializer_class = NoteSerializer
    permission_classes = [IsAuthenticated]


class JWTNotes(Notes):
    authentication_classes = [JWTAuthentication]


class AttributedJWTNotes(JWTNotes):
    # The recipe: DRF has authenticated the user by now.
    async def aperform_create(self, serializer):
        with set_actor(self.request.user):
            await super().aperform_create(serializer)


class FailingNotes(Notes):
    def perform_create(self, serializer):
        with transaction.atomic():
            serializer.save()
            raise ValueError("after the save")


urlpatterns = [
    path("notes/", Notes.as_view({"post": "create"})),
    path("notes/<int:pk>/", Notes.as_view({"patch": "partial_update"})),
    path("jwt/", JWTNotes.as_view({"post": "create"})),
    path("attributed/", AttributedJWTNotes.as_view({"post": "create"})),
    path("failing/", FailingNotes.as_view({"post": "create"})),
]

settings = override_settings(
    ROOT_URLCONF=__name__,
    MIDDLEWARE=[
        "django.contrib.sessions.middleware.SessionMiddleware",
        "django.contrib.auth.middleware.AuthenticationMiddleware",
        "auditlog.middleware.AuditlogMiddleware",
    ],
)


async def entries(**filters):
    return [
        (entry.action, entry.changes_dict, entry.actor_id)
        async for entry in LogEntry.objects.filter(**filters).order_by("pk")
    ]


@settings
class AuditlogTests(UserFixture, TestCase):
    async def test_each_session_users_changes_are_theirs(self):
        other = await User.objects.acreate_user("octavia", password="kindred")
        clients = {}
        for user in (self.user, other):
            clients[user.pk] = AsyncAPIClient()
            await clients[user.pk].aforce_login(user)

        async def edit(user_pk):
            client = clients[user_pk]
            created = await client.post(
                "/notes/", {"title": f"by {user_pk}"}, format="json"
            )
            assert created.status_code == 201, created.data
            patched = await client.patch(
                f"/notes/{created.data['id']}/",
                {"title": f"again {user_pk}"},
                format="json",
            )
            assert patched.status_code == 200, patched.data
            return created.data["id"]

        ids = await asyncio.gather(*(edit(pk) for pk in clients))
        for user_pk, note_id in zip(clients, ids, strict=True):
            actors = {actor for _, _, actor in await entries(object_pk=str(note_id))}
            assert actors == {user_pk}

    async def test_a_masked_field_is_masked(self):
        client = AsyncAPIClient()
        await client.aforce_login(self.user)
        created = await client.post(
            "/notes/", {"title": "t", "secret": "hunter2hunter2"}, format="json"
        )
        assert created.status_code == 201
        ((_, changes, _),) = await entries(object_pk=str(created.data["id"]))
        assert "hunter2hunter2" not in str(changes)

    async def test_a_jwt_user_is_the_actor_only_where_the_view_sets_it(self):
        bearer = f"Bearer {AccessToken.for_user(self.user)}"
        client = AsyncAPIClient()
        plain = await client.post(
            "/jwt/", {"title": "jwt"}, format="json", HTTP_AUTHORIZATION=bearer
        )
        attributed = await client.post(
            "/attributed/", {"title": "set"}, format="json", HTTP_AUTHORIZATION=bearer
        )
        assert (plain.status_code, attributed.status_code) == (201, 201)
        ((_, _, unattributed),) = await entries(object_pk=str(plain.data["id"]))
        ((_, _, actor),) = await entries(object_pk=str(attributed.data["id"]))
        assert unattributed is None
        assert actor == self.user.pk

    async def test_a_rolled_back_change_leaves_no_entry(self):
        client = AsyncAPIClient(raise_request_exception=False)
        await client.aforce_login(self.user)
        before = await sync_to_async(LogEntry.objects.count)()
        response = await client.post("/failing/", {"title": "gone"}, format="json")
        assert response.status_code == 500
        assert await sync_to_async(LogEntry.objects.count)() == before
        assert not await AuditedNote.objects.filter(title="gone").aexists()
