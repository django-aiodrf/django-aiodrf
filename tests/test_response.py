import datetime
import decimal
import json
import threading
import uuid

import pytest
from asgiref.sync import iscoroutinefunction
from django.core.exceptions import SynchronousOnlyOperation
from django.test import Client, override_settings
from django.urls import path
from django.utils.functional import lazy
from rest_framework.renderers import JSONRenderer
from rest_framework.utils.serializer_helpers import ReturnDict

from aiodrf.response import Response
from aiodrf.test import count_hops
from aiodrf.views import APIView
from tests.testapp.models import Author


def json_response():
    response = Response({"a": 1})
    response.accepted_renderer = JSONRenderer()
    response.accepted_media_type = "application/json"
    response.renderer_context = {}
    return response


def test_post_render_callbacks_run_where_django_runs_them():
    # ``cache_page`` stores the response from a post-render callback. On the
    # event loop that would block it (Redis, Memcached) or, for the database
    # cache, raise after the response already counts as rendered.
    response = json_response()
    assert iscoroutinefunction(response.render)
    response.add_post_render_callback(lambda response: None)
    assert not iscoroutinefunction(response.render)


async def test_callback_needing_the_database_is_not_swallowed():
    calls = []

    def callback(response):
        calls.append(response.is_rendered)
        raise SynchronousOnlyOperation("cache.set()")

    response = json_response()
    response.add_post_render_callback(callback)
    # Django's handler renders this response in a thread; called here it
    # must fail loudly rather than skip the callback.
    try:
        response.render()
    except SynchronousOnlyOperation:
        pass
    assert calls == [True]


# -- Nothing of the application's runs twice ----------------------------------------


class Evaluations:
    threads = []


def lazy_value():
    Evaluations.threads.append(threading.get_ident())
    return "resolved"


async def test_a_lazy_value_is_evaluated_once_in_a_thread():
    Evaluations.threads.clear()
    response = json_response()
    response.data = {"value": lazy(lazy_value, str)()}
    rendered = await response.render()
    assert rendered.content == b'{"value":"resolved"}'
    assert Evaluations.threads == [
        t for t in Evaluations.threads if t != threading.get_ident()
    ]
    assert len(Evaluations.threads) == 1


async def test_a_queryset_in_the_data_is_evaluated_once_in_a_thread():
    evaluated = []
    queryset = Author.objects.none()

    def fetch_all():
        # Like Django's: a query the first time, nothing once cached
        # (``tuple()`` asks for ``__len__`` before it iterates on some Pythons).
        if queryset._result_cache is None:
            evaluated.append(threading.get_ident())
            queryset._result_cache = []

    queryset._fetch_all = fetch_all
    response = json_response()
    response.data = {"authors": queryset}
    rendered = await response.render()
    assert rendered.content == b'{"authors":[]}'
    assert len(evaluated) == 1
    assert evaluated[0] != threading.get_ident()


async def test_plain_data_renders_on_the_loop():
    response = json_response()
    response.data = {
        "when": datetime.datetime(2026, 1, 5, tzinfo=datetime.UTC),
        "id": uuid.UUID(int=1),
        "price": decimal.Decimal("1.5"),
        "raw": b"bytes",
        "items": [1, 2.5, None, True, "text"],
    }
    with count_hops() as hops:
        rendered = await response.render()
    assert json.loads(rendered.content) == {
        "when": "2026-01-05T00:00:00Z",
        "id": "00000000-0000-0000-0000-000000000001",
        "price": 1.5,
        "raw": "bytes",
        "items": [1, 2.5, None, True, "text"],
    }
    assert hops.calls == []
    # The real renderer is back on the response afterwards.
    assert type(response.accepted_renderer) is JSONRenderer


def test_a_renderer_declared_pure_is_not_second_guessed():
    class Declared(JSONRenderer):
        async_safe = True

    response = json_response()
    response.accepted_renderer = Declared()
    assert iscoroutinefunction(response.render)
    assert response.render().content == b'{"a":1}'


def test_lazy_render_preserves_the_synchronous_contract():
    response = json_response()
    response.data = {"value": lazy(lambda: "resolved", str)()}
    assert response.render() is response
    assert response.content == b'{"value":"resolved"}'
    assert response.render() is response


class LazyView(APIView):
    authentication_classes = []
    permission_classes = []

    async def get(self, request):
        return Response({"value": lazy(lambda: "resolved", str)()})


urlpatterns = [path("lazy/", LazyView.as_view())]


@override_settings(ROOT_URLCONF=__name__, MIDDLEWARE=[])
def test_lazy_render_through_the_synchronous_handler():
    assert Client().get("/lazy/").json() == {"value": "resolved"}


@pytest.mark.parametrize("kind", ["date", "mapping", "timezone", "return_dict"])
async def test_payload_callbacks_run_once_in_the_worker(kind):
    calls = []

    class Date(datetime.date):
        def isoformat(self):
            calls.append(threading.get_ident())
            return super().isoformat()

    class Mapping(dict):
        def items(self):
            calls.append(threading.get_ident())
            return super().items()

    class Zone(datetime.tzinfo):
        def utcoffset(self, dt):
            calls.append(threading.get_ident())
            return datetime.timedelta(0)

    returned = ReturnDict({"value": 1}, serializer=None)

    def items():
        calls.append(threading.get_ident())
        return dict.items(returned)

    returned.items = items
    value = {
        "date": Date(2026, 9, 22),
        "mapping": Mapping(value=1),
        "timezone": datetime.datetime(2026, 9, 22, tzinfo=Zone()),
        "return_dict": returned,
    }[kind]
    response = json_response()
    response.data = {"first": value, "last": lazy(lambda: "ok", str)()}
    await response.render()
    assert len(calls) == 1
    assert calls[0] != threading.get_ident()
