"""aiodrf.W010: a DRF serializer with async members, used by a DRF view."""

import types

from django.test import override_settings
from django.urls import path
from rest_framework import generics as drf_generics
from rest_framework import serializers as drf_serializers
from rest_framework import viewsets as drf_viewsets
from rest_framework.decorators import action
from rest_framework.response import Response as DRFResponse
from rest_framework.routers import SimpleRouter

from aiodrf import checks, generics, serializers
from tests.testapp.models import Author


class AsyncValidation(drf_serializers.ModelSerializer):
    async def validate_name(self, value):
        return value.strip()

    class Meta:
        model = Author
        fields = ["id", "name"]


class AsyncMethodField(drf_serializers.Serializer):
    upper = drf_serializers.SerializerMethodField()

    async def get_upper(self, author):
        return author.name.upper()


class AiodrfMembers(drf_serializers.Serializer):
    # aiodrf's names: DRF never calls them.
    name = drf_serializers.CharField()

    async def acreate(self, validated_data):
        return validated_data


class Nests(drf_serializers.Serializer):
    author = AsyncValidation()


class Helper(drf_serializers.Serializer):
    # An async helper DRF does not call is the project's.
    name = drf_serializers.CharField()

    async def get_related(self):
        return None


class AiodrfSerializer(serializers.ModelSerializer):
    async def avalidate_name(self, value):
        return value

    async def validate(self, attrs):
        return attrs

    class Meta:
        model = Author
        fields = ["id", "name"]


def drf_view(serializer_class):
    return type(
        "Legacy",
        (drf_generics.ListCreateAPIView,),
        {"serializer_class": serializer_class, "queryset": Author.objects.all()},
    )


def w010(*patterns):
    conf = types.ModuleType("urls")
    conf.urlpatterns = list(patterns)
    with override_settings(ROOT_URLCONF=conf):
        return [
            message
            for message in checks.check_async_serializers_in_drf_views(app_configs=None)
            if message.id == "aiodrf.W010"
        ]


def test_async_validation_in_a_drf_view_is_reported():
    [message] = w010(path("a/", drf_view(AsyncValidation).as_view()))
    assert message.obj is AsyncValidation
    assert "`validate_name`" in message.msg
    assert "Legacy" in message.msg
    assert "coroutine" in message.msg
    assert "aiodrf.serializers" in message.hint


def test_an_async_method_field_is_reported():
    [message] = w010(path("a/", drf_view(AsyncMethodField).as_view()))
    assert "`get_upper`" in message.msg


def test_aiodrfs_async_names_are_reported_as_never_called():
    [message] = w010(path("a/", drf_view(AiodrfMembers).as_view()))
    assert "`acreate`" in message.msg
    assert "never" in message.msg


def test_a_nested_serializer_is_looked_into():
    [message] = w010(path("a/", drf_view(Nests).as_view()))
    assert message.obj is AsyncValidation


def test_an_action_and_as_view_serializer_are_looked_into():
    class Authors(drf_viewsets.GenericViewSet):
        queryset = Author.objects.all()

        @action(detail=False, serializer_class=AsyncMethodField)
        def upper(self, request):
            return DRFResponse({})

    router = SimpleRouter()
    router.register("authors", Authors, basename="authors")
    view = drf_generics.ListAPIView.as_view(
        serializer_class=AsyncValidation, queryset=Author.objects.all()
    )
    messages = w010(*router.urls, path("a/", view))
    assert {message.obj for message in messages} == {AsyncMethodField, AsyncValidation}


def test_each_serializer_is_reported_once():
    view = drf_view(AsyncValidation).as_view()
    assert len(w010(path("a/", view), path("b/", view))) == 1


def test_what_is_not_reported():
    aiodrf_view = type(
        "Modern",
        (generics.ListCreateAPIView,),
        {"serializer_class": AsyncValidation, "queryset": Author.objects.all()},
    )
    assert not w010(
        # aiodrf's generic views await a plain serializer's async members.
        path("a/", aiodrf_view.as_view()),
        # aiodrf serializers bridge them for DRF's callers.
        path("b/", drf_view(AiodrfSerializer).as_view()),
        path("c/", drf_view(Helper).as_view()),
    )
