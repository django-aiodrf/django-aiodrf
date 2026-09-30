"""django-guardian object permissions through djangorestframework-guardian."""

from django.test import TestCase, override_settings
from django.urls import include, path
from guardian.shortcuts import assign_perm
from rest_framework import viewsets as drf_viewsets
from rest_framework.permissions import DjangoObjectPermissions
from rest_framework.routers import SimpleRouter
from rest_framework_guardian.filters import ObjectPermissionsFilter

from aiodrf import viewsets
from aiodrf.contrib import permissions as contrib_permissions
from aiodrf.test import AsyncAPIClient, count_hops
from tests.base import both_transports
from tests.ecosystem.base import UserFixture, same_response
from tests.testapp.models import Author, Book
from tests.testapp.serializers import BookSerializer


class ViewPermissions(DjangoObjectPermissions):
    # DRF's default map lets everyone read; guardian's documentation adds
    # the ``view`` permission like this.
    perms_map = {
        **DjangoObjectPermissions.perms_map,
        "GET": ["%(app_label)s.view_%(model_name)s"],
    }


class ContribViewPermissions(contrib_permissions.DjangoObjectPermissions):
    perms_map = ViewPermissions.perms_map


class Policies:
    queryset = Book.objects.order_by("pk")
    serializer_class = BookSerializer
    permission_classes = [ViewPermissions]
    filter_backends = [ObjectPermissionsFilter]


class DRFBooks(Policies, drf_viewsets.ModelViewSet):
    pass


class Books(Policies, viewsets.ModelViewSet):
    pass


class ContribBooks(Books):
    # ``aiodrf.contrib.permissions``: guardian is asked in the same hop.
    permission_classes = [ContribViewPermissions]


drf_router, router, contrib_router = SimpleRouter(), SimpleRouter(), SimpleRouter()
drf_router.register("books", DRFBooks, basename="drf-book")
router.register("books", Books, basename="book")
contrib_router.register("books", ContribBooks, basename="contrib-book")
urlpatterns = [
    path("drf/", include(drf_router.urls)),
    path("aiodrf/", include(router.urls)),
    path("contrib/", include(contrib_router.urls)),
]

guardian = override_settings(
    ROOT_URLCONF=__name__,
    AUTHENTICATION_BACKENDS=[
        "django.contrib.auth.backends.ModelBackend",
        "guardian.backends.ObjectPermissionBackend",
    ],
)


class BookFixture(UserFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        author = Author.objects.create(name="Ursula")
        cls.visible, cls.hidden = (
            Book.objects.create(title=title, isbn=str(index), author=author)
            for index, title in enumerate(["Visible", "Hidden"])
        )
        assign_perm("testapp.view_book", cls.user)
        assign_perm("testapp.view_book", cls.user, cls.visible)
        assign_perm("testapp.change_book", cls.user)


@both_transports
class _ParityTests(BookFixture):
    @guardian
    async def test_aiodrf_answers_like_drf(self):
        self.client.force_authenticate(self.user)
        requests = [
            ("get", "books/", None),
            ("get", f"books/{self.visible.pk}/", None),
            ("get", f"books/{self.hidden.pk}/", None),
            # ``change`` is granted for the model but not for the object.
            ("patch", f"books/{self.visible.pk}/", {"title": "Changed"}),
        ]
        for method, url, data in requests:
            with self.subTest(method=method, url=url):
                drf = await self.api(method, f"/drf/{url}", data=data)
                aiodrf = await self.api(method, f"/aiodrf/{url}", data=data)
                contrib = await self.api(method, f"/contrib/{url}", data=data)
                assert same_response(drf, aiodrf), (drf.data, aiodrf.data)
                assert same_response(drf, contrib), (drf.data, contrib.data)
        listed = await self.api("get", "/aiodrf/books/")
        assert [book["title"] for book in listed.data] == ["Visible"]
        assert aiodrf.status_code == 403


@guardian
class HopTests(BookFixture, TestCase):
    async def test_permissions_and_the_filtered_list_take_two_hops(self):
        client = AsyncAPIClient()
        client.force_authenticate(self.user)
        with count_hops() as hops:
            response = await client.get("/aiodrf/books/")
        assert len(response.data) == 1
        # ``DjangoObjectPermissions.has_permission`` queries the user's
        # permissions; filtering, the query and the representation share
        # the second hop.
        assert hops.count == 2, hops.calls
