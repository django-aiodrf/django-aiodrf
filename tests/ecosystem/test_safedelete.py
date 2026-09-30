"""
django-safedelete: ``delete()`` hides a row instead of removing it, and the
default manager no longer returns it. aiodrf's generic actions go through the
model's ``delete()`` and the view's queryset, as DRF's do.

``aiodrf.contrib.async_backend``'s native ``async_delete()`` does not call a
model's ``delete()``: soft-deleting models are not supported there.
"""

from asgiref.sync import sync_to_async
from django.test import override_settings
from django.urls import path
from rest_framework import serializers
from rest_framework import viewsets as drf_viewsets
from rest_framework.permissions import AllowAny

from aiodrf import viewsets
from tests.base import both_transports
from tests.ecosystem.models import SoftComment, SoftNote


class NoteSerializer(serializers.ModelSerializer):
    class Meta:
        model = SoftNote
        fields = ["id", "title"]


class CommentSerializer(serializers.ModelSerializer):
    class Meta:
        model = SoftComment
        fields = ["id", "note", "text"]


def viewset(base, model, serializer_class):
    return type(
        f"{model.__name__}ViewSet",
        (base,),
        {
            "__module__": __name__,
            "queryset": model.objects.all(),
            "serializer_class": serializer_class,
            "authentication_classes": [],
            "permission_classes": [AllowAny],
        },
    )


urlpatterns = []
for prefix, base in (
    ("drf", drf_viewsets.ModelViewSet),
    ("aiodrf", viewsets.ModelViewSet),
):
    for name, model, serializer_class in (
        ("notes", SoftNote, NoteSerializer),
        ("comments", SoftComment, CommentSerializer),
    ):
        view = viewset(base, model, serializer_class)
        urlpatterns += [
            path(f"{prefix}/{name}/", view.as_view({"get": "list", "post": "create"})),
            path(
                f"{prefix}/{name}/<int:pk>/",
                view.as_view({"get": "retrieve", "delete": "destroy"}),
            ),
        ]


@both_transports
class _SoftDeleteTests:
    @override_settings(ROOT_URLCONF=__name__)
    async def test_destroy_hides_the_row_as_drf_does(self):
        for prefix in ("drf", "aiodrf"):
            with self.subTest(prefix):
                note = await SoftNote.objects.acreate(title=prefix)
                comment = await SoftComment.objects.acreate(note=note, text="first")
                url = f"/{prefix}/notes/{note.pk}/"
                assert (await self.api("delete", url)).status_code == 204
                # Still there, hidden; its comment follows (SOFT_DELETE_CASCADE).
                assert await SoftNote.all_objects.filter(pk=note.pk).aexists()
                assert not await SoftNote.objects.filter(pk=note.pk).aexists()
                assert not await SoftComment.objects.filter(pk=comment.pk).aexists()
                assert (await self.api("get", url)).status_code == 404
                listed = await self.api("get", f"/{prefix}/notes/")
                assert note.pk not in [row["id"] for row in listed.data]
                # A hidden row cannot be pointed at.
                refused = await self.api(
                    "post",
                    f"/{prefix}/comments/",
                    data={"note": note.pk, "text": "late"},
                )
                assert refused.status_code == 400
                assert "note" in refused.data
                # Restored, it is served again.
                hidden = await SoftNote.all_objects.aget(pk=note.pk)
                await sync_to_async(hidden.undelete)()
                assert (await self.api("get", url)).status_code == 200
