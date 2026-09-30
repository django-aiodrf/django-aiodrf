"""
wireup: dependency injection into class-based views, DRF's and aiodrf's.

wireup's ``WireupConfig.ready()`` replaces the callback of every class-based
view in the URLconf with its own synchronous ``view()`` that builds the view
with the injected dependencies and returns ``dispatch()``. It is set up here
for this module's URLconf only, so that the rest of the suite keeps its URLs.
"""

import inspect
from unittest import mock

from asgiref.sync import iscoroutinefunction
from django.apps import AppConfig
from django.test import TestCase, override_settings
from django.urls import get_resolver, path
from rest_framework import views as drf_views
from rest_framework.permissions import AllowAny
from rest_framework.response import Response as DRFResponse
from wireup import Injected, injectable
from wireup.integration.django import WireupSettings

from aiodrf.response import Response
from aiodrf.test import AsyncAPIClient, count_hops
from aiodrf.views import APIView
from tests.base import both_transports
from tests.settings import MIDDLEWARE


@injectable
class Greeter:
    def greet(self, name):
        return f"Hello, {name}"


class Injectable:
    authentication_classes = []
    permission_classes = [AllowAny]

    def __init__(self, greeter: Injected[Greeter], **kwargs):
        super().__init__(**kwargs)
        self.greeter = greeter


class DRFGreeting(Injectable, drf_views.APIView):
    def get(self, request):
        return DRFResponse({"greeting": self.greeter.greet("Ged")})


class Greeting(Injectable, APIView):
    async def get(self, request):
        return Response({"greeting": self.greeter.greet("Ged")})


urlpatterns = [path("drf/", DRFGreeting.as_view()), path("aiodrf/", Greeting.as_view())]
wired = override_settings(
    ROOT_URLCONF=__name__,
    MIDDLEWARE=["wireup.integration.django.wireup_middleware", *MIDDLEWARE],
    WIREUP=WireupSettings(injectables=[__name__]),
)


class Wired:
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with wired:
            config = AppConfig.create("wireup.integration.django")
            config.ready()  # rewrites the callbacks of this module's URLconf
        patcher = mock.patch(
            "wireup.integration.django.apps.get_app_container",
            return_value=config.container,
        )
        patcher.start()
        cls.addClassCleanup(patcher.stop)

    def test_the_callbacks_were_replaced_by_synchronous_functions(self):
        drf, aiodrf = (
            pattern.callback for pattern in get_resolver(__name__).url_patterns
        )
        for callback in (drf, aiodrf):
            assert callback.__wrapped__.view_class in (DRFGreeting, Greeting)
            assert not callback.__code__.co_flags & inspect.CO_COROUTINE
        # ``functools.wraps`` copied the marker Django reads.
        assert iscoroutinefunction(aiodrf)
        assert not iscoroutinefunction(drf)


@both_transports
class _WireupTests(Wired):
    @wired
    async def test_aiodrf_views_get_their_dependencies_like_drf_views(self):
        # wireup's wrapper is a plain function, but ``functools.wraps`` copies
        # the coroutine marker of the aiodrf view: Django awaits what it
        # returns, which is aiodrf's ``dispatch()`` coroutine.
        for url in ("/drf/", "/aiodrf/"):
            with self.subTest(url=url):
                response = await self.api("get", url)
                assert response.status_code == 200
                assert response.json() == {"greeting": "Hello, Ged"}


@wired
class HopTests(Wired, TestCase):
    async def test_injection_adds_no_hop(self):
        with count_hops() as hops:
            response = await AsyncAPIClient().get("/aiodrf/")
        assert response.status_code == 200
        assert hops.calls == []
