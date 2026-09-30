"""
django-polymorphic with django-rest-polymorphic: one endpoint for a model
hierarchy. ``PolymorphicSerializer`` picks the child serializer per item from
the instance's class or the payload's ``resourcetype``, overriding
``is_valid``, ``run_validation``, ``create`` and ``update``; aiodrf runs these
overrides written for DRF in its worker thread, where the polymorphic
queryset's per-class queries run too.
"""

import pytest
from django.test import override_settings
from django.urls import include, path
from rest_framework import serializers
from rest_framework import viewsets as drf_viewsets
from rest_framework.permissions import AllowAny
from rest_framework.routers import SimpleRouter
from rest_polymorphic.serializers import PolymorphicSerializer

from aiodrf import viewsets
from aiodrf.test import AsyncAPIClient, count_hops
from tests.base import both_transports
from tests.ecosystem.base import same_response
from tests.ecosystem.models import PolyArtProject, PolyProject, PolyResearchProject

# django-rest-polymorphic's serializers define their own to_representation(), so its
# serializers cannot be compiled: the tests run them on DRF's code whatever fallback the
# run's profile sets.
pytestmark = pytest.mark.aiodrf_settings(SERIALIZER_BACKEND_FALLBACK="drf")


class PolyProjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = PolyProject
        fields = ["id", "topic"]


class PolyArtProjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = PolyArtProject
        fields = ["id", "topic", "artist"]


class PolyResearchProjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = PolyResearchProject
        fields = ["id", "topic", "supervisor"]


class PolyProjectPolymorphicSerializer(PolymorphicSerializer):
    model_serializer_mapping = {
        PolyProject: PolyProjectSerializer,
        PolyArtProject: PolyArtProjectSerializer,
        PolyResearchProject: PolyResearchProjectSerializer,
    }


def routes(base):
    router = SimpleRouter()
    router.register(
        "projects",
        type(
            "Projects",
            (base,),
            {
                "authentication_classes": [],
                "permission_classes": [AllowAny],
                "queryset": PolyProject.objects.order_by("pk"),
                "serializer_class": PolyProjectPolymorphicSerializer,
            },
        ),
        basename="project",
    )
    return router.urls


urlpatterns = [
    path("drf/", include((routes(drf_viewsets.ModelViewSet), "drf"))),
    path("aiodrf/", include((routes(viewsets.ModelViewSet), "aiodrf"))),
]
urls = override_settings(ROOT_URLCONF=__name__)


@both_transports
class _PolymorphicTests:
    @classmethod
    def setUpTestData(cls):
        PolyProject.objects.create(topic="Plain")
        PolyArtProject.objects.create(topic="Painting", artist="Frida")
        cls.research = PolyResearchProject.objects.create(
            topic="Stars", supervisor="Vera"
        )

    @urls
    async def test_list_and_retrieve_represent_each_subclass_as_in_drf(self):
        for url in ("projects/", f"projects/{self.research.pk}/"):
            with self.subTest(url=url):
                drf = await self.api("get", f"/drf/{url}")
                aiodrf = await self.api("get", f"/aiodrf/{url}")
                assert drf.status_code == 200
                assert same_response(drf, aiodrf), (drf.data, aiodrf.data)
        listed = (await self.api("get", "/aiodrf/projects/")).data
        assert [item["resourcetype"] for item in listed] == [
            "PolyProject",
            "PolyArtProject",
            "PolyResearchProject",
        ]
        assert listed[1]["artist"] == "Frida"

    @urls
    async def test_create_and_update_pick_the_subclass_from_the_payload(self):
        for name in ("drf", "aiodrf"):
            payload = {
                "resourcetype": "PolyArtProject",
                "topic": name,
                "artist": "Hokusai",
            }
            created = await self.api("post", f"/{name}/projects/", data=payload)
            assert created.status_code == 201, created.data
            assert await PolyArtProject.objects.filter(
                topic=name, artist="Hokusai"
            ).aexists()
            updated = await self.api(
                "patch",
                f"/{name}/projects/{created.data['id']}/",
                data={"resourcetype": "PolyArtProject", "artist": "Hiroshige"},
            )
            assert updated.status_code == 200, updated.data
            assert updated.data["artist"] == "Hiroshige"

    @urls
    async def test_invalid_payloads_are_400_with_drfs_errors(self):
        payloads = [
            {"topic": "No type"},
            {"resourcetype": "Unknown", "topic": "x"},
            {"resourcetype": "PolyResearchProject", "topic": "No supervisor"},
        ]
        for payload in payloads:
            with self.subTest(payload=payload):
                drf = await self.api("post", "/drf/projects/", data=payload)
                aiodrf = await self.api("post", "/aiodrf/projects/", data=payload)
                assert drf.status_code == 400
                assert same_response(drf, aiodrf), (drf.data, aiodrf.data)
        assert await PolyProject.objects.acount() == 3


@pytest.mark.django_db(transaction=True)
async def test_list_and_create_cost_one_hop_each():
    await PolyArtProject.objects.acreate(topic="Painting", artist="Frida")
    client = AsyncAPIClient()
    with override_settings(ROOT_URLCONF=__name__):
        with count_hops() as hops:
            listed = await client.get("/aiodrf/projects/")
        assert listed.status_code == 200
        assert hops.calls == ["ListModelMixin._list"]
        with count_hops() as hops:
            created = await client.post(
                "/aiodrf/projects/",
                {
                    "resourcetype": "PolyResearchProject",
                    "topic": "Stars",
                    "supervisor": "Vera",
                },
                format="json",
            )
        assert created.status_code == 201, created.data
        assert hops.count == 1, hops.calls
