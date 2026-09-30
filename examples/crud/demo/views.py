"""Model CRUD with Django filtering, routers and pagination."""

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.routers import SimpleRouter

from aiodrf import pagination, serializers, viewsets

from .models import Article


class ArticleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Article
        fields = ["id", "title", "tags"]


class Pages(pagination.PageNumberPagination):
    page_size = 2


class Offset(pagination.LimitOffsetPagination):
    default_limit = 2
    max_limit = 10


class Cursor(pagination.CursorPagination):
    page_size = 2
    ordering = "id"


class Articles(viewsets.ModelViewSet):
    queryset = Article.objects.prefetch_related("tags").order_by("id")
    serializer_class = ArticleSerializer
    pagination_class = Pages
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["title"]
    search_fields = ["title"]
    ordering_fields = ["id", "title"]


class Offsets(Articles):
    pagination_class = Offset


class Cursors(Articles):
    pagination_class = Cursor


router = SimpleRouter()
router.register("articles", Articles)
router.register("offset", Offsets, basename="offset")
router.register("cursor", Cursors, basename="cursor")
urlpatterns = router.urls
