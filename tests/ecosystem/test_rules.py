"""rules: ``AutoPermissionViewSetMixin`` and model rule permissions in aiodrf viewsets."""

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import include, path
from rest_framework import serializers
from rest_framework import viewsets as drf_viewsets
from rest_framework.routers import SimpleRouter
from rules.contrib.rest_framework import AutoPermissionViewSetMixin

from aiodrf import viewsets
from aiodrf.test import AsyncAPIClient, count_hops
from tests.base import both_transports
from tests.ecosystem.base import UserFixture, same_response
from tests.ecosystem.models import RulesNote


class NoteSerializer(serializers.ModelSerializer):
    class Meta:
        model = RulesNote
        fields = ["id", "text"]


class Notes(AutoPermissionViewSetMixin):
    serializer_class = NoteSerializer

    def get_queryset(self):
        return RulesNote.objects.order_by("pk")

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)


class DRFNotes(Notes, drf_viewsets.ModelViewSet):
    pass


class AioNotes(Notes, viewsets.ModelViewSet):
    pass


drf_router, router = SimpleRouter(), SimpleRouter()
drf_router.register("notes", DRFNotes, basename="drf-note")
router.register("notes", AioNotes, basename="note")
urlpatterns = [
    path("drf/", include(drf_router.urls)),
    path("aiodrf/", include(router.urls)),
]
rules = override_settings(
    ROOT_URLCONF=__name__,
    AUTHENTICATION_BACKENDS=[
        "rules.permissions.ObjectPermissionBackend",
        "django.contrib.auth.backends.ModelBackend",
    ],
)


class NoteFixture(UserFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        other = User.objects.create_user("ged", password="archmage")
        cls.own = RulesNote.objects.create(owner=cls.user, text="mine")
        cls.foreign = RulesNote.objects.create(owner=other, text="theirs")


@both_transports
class _ParityTests(NoteFixture):
    @rules
    async def test_aiodrf_answers_like_drf(self):
        self.client.force_authenticate(self.user)
        requests = [
            ("get", "notes/", None, 200),
            ("get", f"notes/{self.own.pk}/", None, 200),
            ("get", f"notes/{self.foreign.pk}/", None, 403),
            ("patch", f"notes/{self.foreign.pk}/", {"text": "changed"}, 403),
            ("patch", f"notes/{self.own.pk}/", {"text": "changed"}, 200),
            ("delete", f"notes/{self.foreign.pk}/", None, 403),
            ("get", "notes/999/", None, 404),
        ]
        for method, url, data, status in requests:
            with self.subTest(method=method, url=url):
                kwargs = {} if data is None else {"data": data}
                drf = await self.api(method, f"/drf/{url}", **kwargs)
                aiodrf = await self.api(method, f"/aiodrf/{url}", **kwargs)
                assert same_response(drf, aiodrf), (drf.data, aiodrf.data)
                assert aiodrf.status_code == status
        assert (await RulesNote.objects.aget(pk=self.foreign.pk)).text == "theirs"

    @rules
    async def test_create_and_delete_follow_the_add_and_delete_rules(self):
        anonymous = await self.api("post", "/aiodrf/notes/", data={"text": "new"})
        drf_anonymous = await self.api("post", "/drf/notes/", data={"text": "new"})
        assert same_response(drf_anonymous, anonymous)
        assert anonymous.status_code == 403

        self.client.force_authenticate(self.user)
        created = await self.api("post", "/aiodrf/notes/", data={"text": "new"})
        assert created.status_code == 201, created.data
        deleted = await self.api("delete", f"/aiodrf/notes/{created.data['id']}/")
        assert deleted.status_code == 204


@rules
class HopTests(NoteFixture, TestCase):
    async def test_the_synchronous_initial_override_runs_in_one_extra_hop(self):
        # rules overrides DRF's synchronous ``initial()`` and calls
        # ``get_object()`` there: aiodrf runs it in a worker thread.
        client = AsyncAPIClient()
        client.force_authenticate(self.user)
        with count_hops() as hops:
            response = await client.get(f"/aiodrf/notes/{self.own.pk}/")
        assert response.data == {"id": self.own.pk, "text": "mine"}
        assert hops.count == 2, hops.calls


# ``aiodrf.contrib.permissions`` asks backends through Django's synchronous
# ``has_perms``, so rules' backend, which has no ``ahas_perm``, still counts.


def _note_view(base, permission):
    from rest_framework import mixins

    return type(
        "Notes",
        (mixins.CreateModelMixin, base),
        {
            "queryset": RulesNote.objects.all(),
            "serializer_class": NoteSerializer,
            "permission_classes": [permission],
            "perform_create": lambda self, serializer: serializer.save(
                owner=self.request.user
            ),
        },
    )


@rules
class ContribPermissionTests(UserFixture, TestCase):
    async def test_a_rule_grants_the_model_permission_as_in_drf(self):
        from rest_framework.permissions import DjangoModelPermissions

        from aiodrf.contrib import permissions
        from aiodrf.test import AsyncAPIRequestFactory, force_authenticate

        responses = []
        for base, permission in (
            (drf_viewsets.GenericViewSet, DjangoModelPermissions),
            (viewsets.GenericViewSet, permissions.DjangoModelPermissions),
        ):
            request = AsyncAPIRequestFactory().post("/", {"text": "t"}, format="json")
            force_authenticate(request, self.user)
            view = _note_view(base, permission).as_view({"post": "create"})
            if base is viewsets.GenericViewSet:
                response = await view(request)
            else:
                from asgiref.sync import sync_to_async

                response = await sync_to_async(view)(request)
            responses.append(response.status_code)
        assert responses == [201, 201]

    async def test_djangos_async_permission_api_leaves_the_rule_out(self):
        # Why the contrib does not use ``ahas_perm``: the same user, the
        # same backends, a different answer (docs/upstream/django-auser-has-perm.md).
        from asgiref.sync import sync_to_async

        user = await User.objects.aget(pk=self.user.pk)
        assert await sync_to_async(user.has_perm)("ecosystem.add_rulesnote")
        assert not await user.ahas_perm("ecosystem.add_rulesnote")
