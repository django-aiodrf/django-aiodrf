"""
drf-restwind: templates that replace the Browsable API's. The browsable API
renderer builds its forms by calling the view's synchronous hooks
(``get_serializer``, ``get_queryset``, ``get_object``) while it renders.
"""

import re

import pytest
from django.conf import settings
from django.test import override_settings
from django.urls import include, path
from rest_framework import viewsets as drf_viewsets
from rest_framework.permissions import AllowAny
from rest_framework.renderers import BrowsableAPIRenderer, JSONRenderer
from rest_framework.routers import SimpleRouter

from aiodrf import viewsets
from aiodrf.test import AsyncAPIClient, count_hops
from tests.base import both_transports
from tests.testapp.models import Author
from tests.testapp.serializers import AuthorSerializer


class Policies:
    queryset = Author.objects.order_by("pk")
    serializer_class = AuthorSerializer
    authentication_classes = []
    permission_classes = [AllowAny]
    renderer_classes = [JSONRenderer, BrowsableAPIRenderer]


class DRFAuthors(Policies, drf_viewsets.ModelViewSet):
    pass


class Authors(Policies, viewsets.ModelViewSet):
    pass


drf_router, router = SimpleRouter(), SimpleRouter()
drf_router.register("authors", DRFAuthors, basename="drf-author")
router.register("authors", Authors, basename="author")
urlpatterns = [
    path("drf/", include(drf_router.urls)),
    path("aiodrf/", include(router.urls)),
]
# The app's templates have to come before REST framework's.
restwind = override_settings(
    ROOT_URLCONF=__name__,
    INSTALLED_APPS=["rest_wind", *settings.INSTALLED_APPS],
    STATIC_URL="/static/",
)
STYLESHEET = 'href="/static/rest_wind/css/styles.css"'


def page(response, prefix):
    """The HTML without what differs between two requests and two views."""
    html = response.content.decode()
    html = re.sub(r'name="csrfmiddlewaretoken" value="[^"]+"', "", html)
    html = re.sub(r'"csrfToken": "[^"]+"', "", html)
    return html.replace(f"/{prefix}/", "/<prefix>/").replace("Drf Author", "Author")


@both_transports
class _RestWindTests:
    @classmethod
    def setUpTestData(cls):
        cls.author = Author.objects.create(name="Ursula")

    async def same(self, url):
        drf = await self.api("get", f"/drf/{url}", HTTP_ACCEPT="text/html")
        aiodrf = await self.api("get", f"/aiodrf/{url}", HTTP_ACCEPT="text/html")
        assert aiodrf.status_code == drf.status_code == 200
        assert aiodrf["content-type"] == "text/html; charset=utf-8"
        assert page(aiodrf, "aiodrf") == page(drf, "drf")
        return aiodrf.content.decode()

    @restwind
    async def test_the_list_page_and_its_create_form(self):
        html = await self.same("authors/")
        assert STYLESHEET in html
        assert "Ursula" in html
        # The HTML form built from the serializer, and the raw data form.
        assert 'name="name"' in html
        assert 'id="id__content"' in html

    @restwind
    async def test_the_detail_page_and_its_update_form(self):
        html = await self.same(f"authors/{self.author.pk}/")
        assert STYLESHEET in html
        assert 'value="Ursula"' in html


@restwind
@pytest.mark.django_db(transaction=True)
async def test_the_page_costs_the_list_hop_and_the_forms_in_django_s_render_thread():
    await Author.objects.acreate(name="Ursula")
    with count_hops() as hops:
        response = await AsyncAPIClient().get(
            "/aiodrf/authors/", HTTP_ACCEPT="text/html"
        )
    assert response.status_code == 200
    # The renderer's calls into the view (forms, the serializer) happen while
    # Django renders the template response in a thread, not in aiodrf hops.
    assert hops.calls == ["ListModelMixin._list"]
