"""ObjectId serialization and MongoDB-backed CRUD endpoints."""

from aiodrf_asgi_lifespan.asgi import get_lifespan_state
from django.urls import path
from django_mongodb_extensions.rest_framework import MongoModelSerializer
from rest_framework.routers import SimpleRouter

from aiodrf import serializers, viewsets
from aiodrf.contrib.mongodb.fields import ObjectIdPrimaryKeyRelatedField
from aiodrf.response import Response
from aiodrf.views import APIView

from .lifecycle import Resources
from .models import Note


class NoteSerializer(MongoModelSerializer, serializers.ModelSerializer):
    serializer_related_field = ObjectIdPrimaryKeyRelatedField

    class Meta:
        model = Note
        fields = ["id", "title"]


class Notes(viewsets.ModelViewSet):
    queryset = Note.objects.order_by("id")
    serializer_class = NoteSerializer


class NativeNoteInput(serializers.Serializer):
    title = serializers.CharField(max_length=100)


class NativeNotes(APIView):
    """Direct driver access retains DRF input validation, not ORM hooks."""

    async def get(self, request):
        resources = get_lifespan_state(request, Resources)
        collection = resources.mongo[resources.database][Note._meta.db_table]
        async with collection.find({}, {"title": 1}).sort("_id", 1).limit(20) as cursor:
            rows = [
                {"id": str(row["_id"]), "title": row["title"]} async for row in cursor
            ]
        return Response(rows)

    async def post(self, request):
        serializer = NativeNoteInput(data=await request.adata())
        await serializer.ais_valid(raise_exception=True)
        resources = get_lifespan_state(request, Resources)
        collection = resources.mongo[resources.database][Note._meta.db_table]
        result = await collection.insert_one(dict(serializer.validated_data))
        return Response(
            {"id": str(result.inserted_id), **serializer.validated_data}, status=201
        )


router = SimpleRouter()
router.register("notes", Notes)
urlpatterns = [path("native-notes/", NativeNotes.as_view()), *router.urls]
