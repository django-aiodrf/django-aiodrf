"""DRF model serializers with compiler and field-cache profiles."""

from django.conf import settings
from django.urls import path
from rest_framework.decorators import action
from rest_framework.routers import SimpleRouter

from aiodrf import aio, serializers, viewsets
from aiodrf.response import Response
from aiodrf.views import APIView

from .models import Article, Tag


class TagOutput(serializers.ModelSerializer):
    class Meta:
        model = Tag
        fields = ["id", "name"]


class Write(serializers.ModelSerializer):
    class Meta:
        model = Article
        fields = ["id", "title", "tags"]


class Read(serializers.ModelSerializer):
    tags = TagOutput(many=True, read_only=True)

    class Meta:
        model = Article
        fields = ["id", "title", "tags"]
        auto_prefetch = True


class PerSerializer(Read):
    class Meta(Read.Meta):
        serializer_backend = "pydantic"
        cache_fields = True
        field_copy_mode = "compiled"


class Articles(viewsets.ModelViewSet):
    queryset = Article.objects.prefetch_related("tags").order_by("id")
    serializer_class = Read

    def get_serializer_class(self):
        return Write if self.action in ("create", "update", "partial_update") else Read

    async def create(self, request, *args, **kwargs):
        write = await self.aget_serializer(data=request.data)
        await aio.is_valid(write, raise_exception=True)
        await self.aperform_create(write)
        read = Read(write.instance, context=await self.aget_serializer_context())
        data = await aio.data(read)
        return Response(data, status=201, headers=self.get_success_headers(data))

    @action(detail=True)
    async def explicit_backend(self, request, pk=None):
        row = await self.aget_object()
        return Response(await aio.data(PerSerializer(row)))


class SelectedArticles(Articles):
    serializer_field_cache = True
    serializer_field_copy_mode = "compiled"


class Profile(APIView):
    async def get(self, request):
        return Response({"profile": settings.PROFILE, "options": settings.FASTDRF})


router = SimpleRouter()
router.register("articles", Articles)
router.register("selected-articles", SelectedArticles, basename="selected-article")
urlpatterns = [*router.urls, path("profile/", Profile.as_view())]
