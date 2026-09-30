"""CRUD endpoints using the opt-in native PostgreSQL backend."""

from rest_framework.routers import SimpleRouter

from aiodrf.contrib import async_backend as native

from .models import Note


class NoteSerializer(native.ModelSerializer):
    class Meta:
        model = Note
        fields = ["id", "title"]


class Notes(native.ModelViewSet):
    queryset = Note.async_objects.order_by("id")
    serializer_class = NoteSerializer


router = SimpleRouter()
router.register("notes", Notes)
urlpatterns = router.urls
