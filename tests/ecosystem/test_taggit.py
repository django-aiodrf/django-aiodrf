"""
django-taggit's ``TaggitSerializer`` pops the tags from ``validated_data``,
saves the instance and then sets the tags (``manager.set``), all in
``save()``; ``TagListSerializerField`` reads ``manager.all()``, which a
``prefetch_related("tags")`` answers.
"""

import pytest
from django.test import TestCase, override_settings
from django.urls import path
from rest_framework import serializers
from rest_framework import viewsets as drf_viewsets
from rest_framework.permissions import AllowAny
from taggit.serializers import TaggitSerializer, TagListSerializerField

from aiodrf import viewsets
from aiodrf.test import APIClient, count_hops
from tests.base import both_transports
from tests.ecosystem.models import TaggedArticle

# django-taggit's TagListSerializerField is not DRF's, so its serializers cannot be
# compiled: the tests run them on DRF's code whatever fallback the run's profile sets.
pytestmark = pytest.mark.aiodrf_settings(SERIALIZER_BACKEND_FALLBACK="drf")


class ArticleSerializer(TaggitSerializer, serializers.ModelSerializer):
    tags = TagListSerializerField()

    class Meta:
        model = TaggedArticle
        fields = ["id", "title", "tags"]


class PrefetchedArticleSerializer(ArticleSerializer):
    class Meta(ArticleSerializer.Meta):
        auto_prefetch = True


class Policies:
    serializer_class = ArticleSerializer
    authentication_classes = []
    permission_classes = [AllowAny]


class DRFArticles(Policies, drf_viewsets.ModelViewSet):
    queryset = TaggedArticle.objects.prefetch_related("tags").order_by("pk")


class Articles(Policies, viewsets.ModelViewSet):
    queryset = TaggedArticle.objects.order_by("pk")


class PrefetchedArticles(Articles):
    serializer_class = PrefetchedArticleSerializer


routes = {"get": "list", "post": "create"}
detail = {"get": "retrieve", "put": "update"}
urlpatterns = [
    path("drf/", DRFArticles.as_view(routes)),
    path("drf/<int:pk>/", DRFArticles.as_view(detail)),
    path("aiodrf/", Articles.as_view(routes)),
    path("aiodrf/<int:pk>/", Articles.as_view(detail)),
    path("prefetched/", PrefetchedArticles.as_view(routes)),
]
urls = override_settings(ROOT_URLCONF=__name__)


def tags(response):
    return sorted(response.data["tags"])


@both_transports
class _TaggitTests:
    @urls
    async def test_tags_are_saved_with_the_instance(self):
        payload = {"title": "Earthsea", "tags": ["wizard", "sea"]}
        drf = await self.api("post", "/drf/", data=payload)
        with count_hops() as hops:
            aiodrf = await self.api("post", "/aiodrf/", data=payload)
        assert aiodrf.status_code == drf.status_code == 201
        assert tags(aiodrf) == tags(drf) == ["sea", "wizard"]
        if self.transport == "asgi":
            # Save and tag setting in the create's one hop. The field returns
            # taggit's ``TagList``, a list subclass, which aiodrf does not
            # render on the loop (only plain JSON types are), so rendering
            # is a second hop.
            assert hops.calls == [
                "CreateModelMixin._create",
                "SimpleTemplateResponse.render",
            ]
        article = await TaggedArticle.objects.aget(pk=aiodrf.data["id"])
        assert sorted([tag.name async for tag in article.tags.all()]) == [
            "sea",
            "wizard",
        ]

        replaced = {"title": "Earthsea", "tags": ["dragon"]}
        drf = await self.api("put", f"/drf/{drf.data['id']}/", data=replaced)
        aiodrf = await self.api("put", f"/aiodrf/{article.pk}/", data=replaced)
        assert aiodrf.status_code == drf.status_code == 200
        assert tags(aiodrf) == tags(drf) == ["dragon"]
        assert [tag.name async for tag in article.tags.all()] == ["dragon"]

    @urls
    async def test_invalid_tag_lists_are_drfs_400(self):
        for value in ({"a": 1}, [1, 2], "not json"):
            with self.subTest(value=value):
                payload = {"title": "Earthsea", "tags": value}
                drf = await self.api("post", "/drf/", data=payload)
                aiodrf = await self.api("post", "/aiodrf/", data=payload)
                assert aiodrf.status_code == drf.status_code == 400
                assert aiodrf.data == drf.data
        assert await TaggedArticle.objects.acount() == 0


@urls
class TagQueryTests(TestCase):
    # Through WSGI, where the queries run on this thread's connection.
    @classmethod
    def setUpTestData(cls):
        for number in range(4):
            TaggedArticle.objects.create(title=f"a{number}").tags.set(
                ["x", f"t{number}"]
            )

    def test_auto_prefetch_loads_the_tags_like_prefetch_related(self):
        client = APIClient()
        with self.assertNumQueries(2):
            drf = client.get("/drf/")
        with self.assertNumQueries(2):
            prefetched = client.get("/prefetched/")
        with self.assertNumQueries(1 + 4):
            plain = client.get("/aiodrf/")
        assert prefetched.data == drf.data
        assert [sorted(item["tags"]) for item in plain.data] == [
            sorted(item["tags"]) for item in drf.data
        ]
