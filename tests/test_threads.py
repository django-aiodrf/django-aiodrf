"""
Shared state under threads.

aiodrf's code runs in more than one thread: worker threads of
``sync_to_async``, one event loop per request thread under WSGI, and any
number of both on a free-threaded interpreter. These tests start the racing
threads together on a barrier; they pass on every interpreter and are
meaningful on ``python3.14t``.
"""

import itertools
import sys
import sysconfig
import threading

import msgspec
import pytest
from django.test import override_settings
from fastdrf import compiler, inputs, typed
from rest_framework import permissions
from rest_framework import serializers as drf_serializers

from aiodrf.aio import _classify
from aiodrf.settings import aiodrf_settings
from aiodrf.utils import HopCounter, Impl, resolve_pair
from tests.testapp.models import Edition

THREADS = 16


def test_free_threaded_interpreter_has_not_reenabled_the_gil():
    if sysconfig.get_config_var("Py_GIL_DISABLED"):
        assert not sys._is_gil_enabled()


def race(work, arguments=None):
    """Run ``work`` in ``THREADS`` threads that start together; return the results."""
    arguments = arguments or [()] * THREADS
    barrier = threading.Barrier(len(arguments))
    results = [None] * len(arguments)
    errors = []

    def run(index, args):
        try:
            barrier.wait(timeout=30)
            results[index] = work(*args)
        except BaseException as exc:  # noqa: BLE001 -- reported below
            errors.append(exc)

    threads = [threading.Thread(target=run, args=item) for item in enumerate(arguments)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    return results


def test_pair_resolution():
    class Custom(permissions.BasePermission):
        def has_permission(self, request, view):
            return True

    resolve_pair.cache_clear()
    results = race(lambda: resolve_pair(Custom, "has_permission", "ahas_permission"))
    assert set(results) == {Impl.SYNC}


@pytest.mark.aiodrf_settings(REPRESENTATION_MODE="thread")
def test_settings_reload_while_reading():
    def read():
        for _ in range(200):
            assert aiodrf_settings.REPRESENTATION_MODE in ("thread", "inline")
            assert isinstance(aiodrf_settings.pure_classes, frozenset)

    def reload():
        for _ in range(200):
            aiodrf_settings.reload()

    race(lambda work: work(), [(read,), (reload,)] * (THREADS // 2))
    # A value read before a reload must not have been cached after it: such
    # a value is never cleared again, and settings changes stop applying.
    with override_settings(
        AIODRF={"REPRESENTATION_MODE": "inline", "PURE_POLICIES": [FieldSubset]},
        FASTDRF={},
    ):
        assert aiodrf_settings.REPRESENTATION_MODE == "inline"
        assert FieldSubset in aiodrf_settings.pure_classes
    assert aiodrf_settings.REPRESENTATION_MODE == "thread"
    assert FieldSubset not in aiodrf_settings.pure_classes


class FieldSubset(drf_serializers.ModelSerializer):
    def __init__(self, *args, fields, **kwargs):
        super().__init__(*args, **kwargs)
        for name in set(self.fields) - set(fields):
            self.fields.pop(name)

    class Meta:
        model = Edition
        fields = ["id", "code", "released", "active", "rating", "format", "notes"]


@pytest.mark.parametrize("backend", ["msgspec", "pydantic"])
def test_compiled_variants_stay_bounded(backend):
    names = FieldSubset.Meta.fields
    subsets = [*itertools.combinations(names, 2), *itertools.combinations(names, 3)]
    assert len(subsets) > compiler.MAX_VARIANTS + THREADS
    with override_settings(FASTDRF={"SERIALIZER_BACKEND": backend}, AIODRF={}):
        for start in range(0, len(subsets), THREADS):
            batch = subsets[start : start + THREADS]
            race(
                lambda subset: compiler.compiled_for(FieldSubset(fields=subset)),
                [(s,) for s in batch],
            )
    assert len(compiler._compiled.get(FieldSubset)) == compiler.MAX_VARIANTS


class InputSubset(drf_serializers.Serializer):
    a = drf_serializers.IntegerField()
    b = drf_serializers.IntegerField()
    c = drf_serializers.IntegerField()
    d = drf_serializers.IntegerField()
    e = drf_serializers.IntegerField()
    f = drf_serializers.IntegerField()
    g = drf_serializers.IntegerField()

    def __init__(self, *args, keep, **kwargs):
        super().__init__(*args, **kwargs)
        for name in set(self.fields) - set(keep):
            self.fields.pop(name)


@pytest.mark.parametrize("backend", ["msgspec", "pydantic"])
def test_recognizer_variants_stay_bounded(backend):
    # (``__init__`` is not a validation method, so the class stays eligible.)
    subsets = [
        *itertools.combinations("abcdefg", 2),
        *itertools.combinations("abcdefg", 3),
    ]
    for start in range(0, len(subsets), THREADS):
        batch = subsets[start : start + THREADS]
        race(
            lambda keep: inputs.recognize(
                InputSubset(data=dict.fromkeys(keep, 1), keep=keep), backend=backend
            ),
            [(keep,) for keep in batch],
        )
    assert len(inputs._recognizers.get(InputSubset)) == compiler.MAX_VARIANTS


class RacedChild(drf_serializers.Serializer):
    number = drf_serializers.IntegerField()

    async def get_number(self, instance):
        return instance["number"]


class RacedParent(drf_serializers.Serializer):
    child = RacedChild()
    children = RacedChild(many=True)


def test_nested_classification_per_class_keeps_instance_edits_apart():
    # Every thread classifies from a cold class cache; half of them edited
    # a nested serializer's fields on their own instance first.
    def classify(edited):
        serializer = RacedParent({"child": {"number": 1}, "children": []})
        if edited:
            serializer.fields["child"].fields["number"] = (
                drf_serializers.SerializerMethodField()
            )
        return _classify.has_async_representation(serializer)

    _classify._CLASS_KINDS.clear()
    edits = [(index % 2 == 0,) for index in range(THREADS)]
    assert race(classify, edits) == [edited for (edited,) in edits]
    assert race(lambda: classify(False)) == [False] * THREADS
    assert _classify._CLASS_KINDS[RacedParent] == {"representation": False}


def test_one_adapted_class_per_schema():
    schema = msgspec.defstruct("Raced", [("value", int)])
    classes = race(lambda: typed.adapt(schema))
    assert len({id(cls) for cls in classes}) == 1


def test_hop_counter_loses_nothing():
    counter = HopCounter()

    def count():
        for _ in range(1000):
            counter._record("hop")

    race(count)
    assert counter.count == THREADS * 1000


def test_closing_hop_counter_stops_all_workers():
    counter = HopCounter()

    def record_or_close(close):
        if close:
            counter._close()
            return counter.count
        for _ in range(100):
            counter._record("hop")
        return None

    closed_at, *_ = race(record_or_close, [(True,), *[(False,)] * (THREADS - 1)])
    assert counter.count == closed_at


def test_compiler_cache_shares_a_bucket_under_threads():
    cache = compiler._SerializerCache()
    buckets = race(lambda: cache.get_or_create(FieldSubset))
    assert all(bucket is buckets[0] for bucket in buckets)


def test_negotiation_capacity_is_enforced_under_threads(monkeypatch):
    from rest_framework.renderers import JSONRenderer
    from rest_framework.test import APIRequestFactory

    from aiodrf import views
    from aiodrf.request import Request

    monkeypatch.setattr(views, "_negotiations", {})
    monkeypatch.setattr(views, "_NEGOTIATION_CACHE_SIZE", 4)

    def negotiate(index):
        view = views.APIView()
        view.renderer_classes = [JSONRenderer]
        view.format_kwarg = None
        accept = f"application/json; v={index}"
        request = Request(APIRequestFactory().get("/", HTTP_ACCEPT=accept))
        renderer, media_type = view._perform_content_negotiation(request, False)
        assert type(renderer) is JSONRenderer
        assert media_type == accept
        with views._negotiation_lock:
            assert len(views._negotiations) <= 4

    race(negotiate, [(index,) for index in range(THREADS)])
