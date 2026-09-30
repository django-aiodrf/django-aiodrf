"""aiodrf.W006: adrf's action names on a view aiodrf routes by DRF's names."""

import types
import warnings

from django.core.checks import Tags, run_checks
from django.test import override_settings
from django.urls import path
from rest_framework import serializers

from aiodrf import checks, routers, viewsets
from aiodrf.contrib import adrf_compat
from aiodrf.response import Response
from aiodrf.views import APIView
from tests.testapp.models import Author


class AuthorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Author
        fields = ["name"]


class Authors(viewsets.ModelViewSet):
    queryset = Author.objects.all()
    serializer_class = AuthorSerializer


def urlconf(*patterns, viewsets=()):
    router = routers.SimpleRouter()
    for index, viewset in enumerate(viewsets):
        router.register(f"v{index}", viewset, basename=f"v{index}")
    conf = types.ModuleType("urls")
    conf.urlpatterns = [*patterns, *router.urls]
    return conf


def w006(conf):
    with override_settings(ROOT_URLCONF=conf):
        return [
            message
            for message in checks.check_view_names(app_configs=None)
            if message.id == "aiodrf.W006"
        ]


def test_an_adrf_action_on_a_viewset_is_reported_once_per_view():
    class Books(Authors):
        async def alist(self, request, *args, **kwargs):
            return Response({"custom": True})

    # The router makes a list and a detail route for the class: one warning.
    [message] = w006(urlconf(viewsets=[Books]))
    assert message.obj is Books
    assert "Books.alist" in message.msg
    assert "`list`" in message.msg
    assert "python -m aiodrf.codemod" in message.hint


def test_every_adrf_action_name_is_reported():
    class Books(Authors):
        async def aretrieve(self, request, *args, **kwargs): ...

        async def partial_aupdate(self, request, *args, **kwargs): ...

        def adestroy(self, request, *args, **kwargs): ...

    [message] = w006(urlconf(viewsets=[Books]))
    for name in ("aretrieve", "partial_aupdate", "adestroy"):
        assert name in message.msg


def test_an_inherited_adrf_action_is_reported_on_the_routed_view():
    class Base(Authors):
        async def acreate(self, request, *args, **kwargs): ...

    class Books(Base):
        pass

    [message] = w006(urlconf(viewsets=[Books]))
    assert message.obj is Books
    assert "acreate" in message.msg


def test_an_api_view_is_checked_too():
    class Plain(APIView):
        async def alist(self, request):
            return Response()

    [message] = w006(urlconf(path("plain/", Plain.as_view())))
    assert message.obj is Plain


def test_serializer_shaped_methods_and_drf_names_are_not_reported():
    class Books(Authors):
        # A write pair with a serializer's signature, not an action.
        async def acreate(self, validated_data): ...

        async def aupdate(self, instance, validated_data): ...

        async def list(self, request, *args, **kwargs):
            return Response()

        async def aperform_create(self, serializer): ...

    assert w006(urlconf(viewsets=[Books])) == []


def test_an_adrf_compat_view_is_not_reported():
    from aiodrf.contrib.adrf_compat.adrf import viewsets as adrf_viewsets

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", adrf_compat.AdrfCompatWarning)

        class Books(adrf_viewsets.ModelViewSet):
            queryset = Author.objects.all()
            serializer_class = AuthorSerializer

            async def alist(self, request, *args, **kwargs):
                return Response()

    assert w006(urlconf(viewsets=[Books])) == []


def test_nothing_is_reported_while_the_adrf_layer_is_installed(monkeypatch):
    class Books(Authors):
        async def alist(self, request, *args, **kwargs): ...

    monkeypatch.setattr(adrf_compat, "is_installed", lambda: True)
    assert w006(urlconf(viewsets=[Books])) == []


def test_the_check_is_registered_with_the_url_checks():
    class Books(Authors):
        async def alist(self, request, *args, **kwargs): ...

    with override_settings(ROOT_URLCONF=urlconf(viewsets=[Books])):
        ids = [message.id for message in run_checks(tags=[Tags.urls])]
    assert "aiodrf.W006" in ids


def test_an_adrf_action_with_adrfs_own_signature_is_reported():
    # adrf declares ``ListModelMixin.alist(self, *args, **kwargs)``.
    class Books(Authors):
        async def alist(self, *args, **kwargs):
            return Response({"custom": True})

    [message] = w006(urlconf(viewsets=[Books]))
    assert "Books.alist" in message.msg


def test_the_url_checks_pass_a_project_without_a_urlconf():
    # Django's own URL checks answer [] without ROOT_URLCONF.
    from django.conf import settings
    from django.urls import clear_url_caches

    with override_settings():
        del settings.ROOT_URLCONF
        clear_url_caches()
        try:
            assert run_checks(tags=[Tags.urls]) == []
        finally:
            clear_url_caches()
