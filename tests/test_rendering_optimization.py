"""Rendering optimizations preserve DRF bytes and the worker safety boundary."""

import datetime
import decimal
import gc
import threading
import uuid
import weakref
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

import pytest
from asgiref.sync import iscoroutinefunction
from django.utils.functional import lazy
from rest_framework.exceptions import ErrorDetail
from rest_framework.renderers import JSONRenderer
from rest_framework.utils.encoders import JSONEncoder as DRFEncoder
from rest_framework.utils.serializer_helpers import ReturnDict, ReturnList

from aiodrf.contrib.msgspec.renderers import MsgspecJSONRenderer
from aiodrf.response import Response, _plain_data
from aiodrf.test import count_hops


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        False,
        42,
        1.5,
        "value",
        b"value",
        ErrorDetail("bad"),
        decimal.Decimal("1.50"),
        uuid.UUID(int=1),
        datetime.date(2026, 9, 28),
        datetime.timedelta(seconds=1),
        datetime.time(12, 30),
        datetime.datetime(2026, 9, 28),
        datetime.datetime(2026, 9, 28, tzinfo=datetime.UTC),
    ],
)
def test_plain_scalar_classification_inside_mixed_containers(value):
    assert _plain_data({"rows": [({"value": value},)]})


def test_deep_payload_classification_does_not_use_the_python_call_stack():
    root = leaf = []
    for _ in range(2000):
        child = []
        leaf.append({"child": child})
        leaf = child
    leaf.append(root)
    assert _plain_data(root)
    leaf.append(object())
    assert not _plain_data(root)


@pytest.mark.parametrize("renderer_class", [JSONRenderer, MsgspecJSONRenderer])
@pytest.mark.parametrize("hook", ["render", "get_indent", "encoder"])
async def test_instance_renderer_callbacks_use_the_worker(renderer_class, hook):
    from asgiref.sync import sync_to_async

    from aiodrf.response import _arender

    renderer = renderer_class()
    threads = []
    if hook == "encoder":
        if renderer_class is JSONRenderer:
            parent = renderer.encoder_class

            class Encoder(parent):
                def encode(self, data):
                    threads.append(threading.get_ident())
                    return super().encode(data)

            renderer.encoder_class = Encoder
        else:
            parent = renderer.encoder

            class Encoder:
                def encode(self, data):
                    threads.append(threading.get_ident())
                    return parent.encode(data)

            renderer.encoder = Encoder()
    else:
        original = getattr(renderer, hook)

        def callback(*args, **kwargs):
            threads.append(threading.get_ident())
            return original(*args, **kwargs)

        setattr(renderer, hook, callback)
    response = Response({"value": 1})
    response.accepted_renderer = renderer
    response.accepted_media_type = "application/json"
    response.renderer_context = {}
    # This is the choice Django's async handler uses before rendering.
    assert not iscoroutinefunction(response.render)
    await sync_to_async(response.render)()
    with count_hops() as hops:
        assert await _arender(renderer, {"value": 1}) == response.content
    assert hops.count == 1
    assert threads
    assert all(thread != threading.get_ident() for thread in threads)


@pytest.mark.parametrize("size", [1024, 8 * 1024 * 1024])
@pytest.mark.parametrize("accept", ["application/json", "application/json; indent=4"])
def test_large_plain_json_matches_drf_bytes_with_and_without_indentation(size, accept):
    data = {
        "text": "x" * size,
        "unicode": "ş🙂\u2028\u2029",
        "flag": True,
        "missing": None,
    }
    assert MsgspecJSONRenderer().render(data, accept) == JSONRenderer().render(
        data, accept
    )


@pytest.mark.parametrize(
    "data",
    [
        {"text": "ş🙂\u2028\u2029", "n": 1.5, "big": 10**30, "none": None},
        [1, "two", [3.0, True], {"nested": {"deep": []}}],
        {"when": datetime.datetime(2026, 9, 29, tzinfo=datetime.UTC)},
        {"amount": decimal.Decimal("1.50"), "id": uuid.UUID(int=1)},
        None,
    ],
)
@pytest.mark.parametrize(
    ("accept", "context"),
    [
        ("application/json", {}),
        ("application/json", {"indent": 0}),
        ("application/json", {"indent": 2}),
        ("application/json; indent=4", {}),
        (None, None),
    ],
)
def test_the_kept_encoder_renders_drfs_bytes(data, accept, context):
    from aiodrf.response import _KeptEncoderJSONRenderer

    assert _KeptEncoderJSONRenderer().render(
        data, accept, context
    ) == JSONRenderer().render(data, accept, context)


@pytest.mark.parametrize(
    "data",
    [
        {"text": "ş🙂\u2028\u2029", "n": 1.5, "big": 10**30, "none": None},
        [1, "two", [3.0, True], {"nested": {"deep": []}}],
        None,
    ],
)
@pytest.mark.parametrize(
    ("accept", "context"),
    [
        ("application/json", {}),
        ("application/json", {"indent": 0}),
        ("application/json", {"indent": 2}),
        ("application/json; indent=4", {}),
        ("application/json; charset=utf-8", {}),
        (None, None),
    ],
)
def test_the_kept_msgspec_renderer_renders_its_bytes(data, accept, context):
    from aiodrf.contrib.msgspec.renderers import MsgspecJSONRenderer
    from aiodrf.response import _KEPT_ENCODER_RENDERERS

    kept = _KEPT_ENCODER_RENDERERS[MsgspecJSONRenderer]
    assert type(kept) is not MsgspecJSONRenderer
    assert kept.render(data, accept, context) == MsgspecJSONRenderer().render(
        data, accept, context
    )


async def test_built_in_values_render_inline_as_drfs_encoder_renders_them():
    # ``marshal`` accepts sets and bytes; DRF's encoder converts them.
    response = Response({"tags": {1}, "raw": b"x", "pair": (1, 2)})
    response.accepted_renderer = JSONRenderer()
    response.accepted_media_type = "application/json"
    response.renderer_context = {}
    with count_hops() as hops:
        await response.render()
    assert hops.count == 0
    assert response.content == JSONRenderer().render(response.data)


def test_explicit_renderer_override_selects_djangos_worker_path():
    class ThreadedJSONRenderer(JSONRenderer):
        def render(self, data, accepted_media_type=None, renderer_context=None):
            return super().render(data, accepted_media_type, renderer_context)

    response = Response({"text": "x"})
    response.accepted_renderer = ThreadedJSONRenderer()
    response.accepted_media_type = "application/json"
    response.renderer_context = {}
    assert not iscoroutinefunction(response.render)
    assert response.render().content == b'{"text":"x"}'


@pytest.mark.parametrize("container", [list, tuple, ReturnList])
@pytest.mark.parametrize("mapping", [dict, ReturnDict, OrderedDict])
async def test_nested_plain_payload_keeps_drf_bytes_and_needs_no_worker(
    container, mapping
):
    row = {"id": 1, "when": datetime.datetime(2026, 9, 23, tzinfo=datetime.UTC)}
    row = mapping(row, serializer=None) if mapping is ReturnDict else mapping(row)
    rows = (
        container([row] * 100, serializer=None)
        if container is ReturnList
        else container([row] * 100)
    )
    response = Response({"rows": rows})
    response.accepted_renderer = JSONRenderer()
    response.accepted_media_type = "application/json"
    response.renderer_context = {}
    with count_hops() as hops:
        await response.render()
    assert hops.count == 0
    assert response.content == JSONRenderer().render(response.data)


@pytest.mark.parametrize("position", [0, 50, 99])
async def test_unknown_leaf_anywhere_runs_only_in_worker(position):
    evaluated = []

    def value():
        evaluated.append(threading.get_ident())
        return "resolved"

    data = [{"items": [1, 2, 3]} for _ in range(100)]
    data[position]["items"].append(lazy(value, str)())
    response = Response(data)
    response.accepted_renderer = JSONRenderer()
    response.accepted_media_type = "application/json"
    response.renderer_context = {}
    with count_hops() as hops:
        await response.render()
    assert hops.count == 1
    assert len(evaluated) == 1
    assert evaluated[0] != threading.get_ident()
    assert b"resolved" in response.content


def test_cycle_detection_does_not_hide_unknown_siblings_or_replace_encoder_errors():
    cyclic = []
    cyclic.append(cyclic)
    assert _plain_data(cyclic)
    assert not _plain_data([cyclic, object()])
    with pytest.raises(ValueError, match="Circular reference"):
        JSONRenderer().render(cyclic)


def test_payload_inspection_is_per_call_and_shared_by_no_request():
    payload = {"rows": [{"value": 1}] * 100}
    with ThreadPoolExecutor(max_workers=8) as workers:
        assert all(workers.map(_plain_data, [payload] * 100))
    payload["rows"][0]["value"] = object()
    assert not _plain_data(payload)


def test_container_and_key_callbacks_are_not_used_during_inspection():
    class Mapping(dict):
        def items(self):
            raise AssertionError("callback evaluated")

    class Key(str):
        __slots__ = ()

        def __str__(self):
            raise AssertionError("callback evaluated")

    assert not _plain_data({"rows": [Mapping(value=1)]})
    assert not _plain_data({Key("key"): 1})
    returned = ReturnDict({"value": 1}, serializer=None)
    returned.items = lambda: (_ for _ in ()).throw(AssertionError("callback evaluated"))
    assert not _plain_data(returned)
    ordered = OrderedDict(value=1)
    ordered.items = lambda: (_ for _ in ()).throw(AssertionError("callback evaluated"))
    assert not _plain_data(ordered)


@pytest.mark.parametrize("position", ["value", "key", "timezone"])
def test_payload_type_checks_do_not_call_custom_metaclasses(position):
    calls = []

    class CallbackType(type):
        def __hash__(cls):
            calls.append("hash")
            return type.__hash__(cls)

        def __eq__(cls, other):
            calls.append("eq")
            return type.__eq__(cls, other)

    class Value(metaclass=CallbackType):
        pass

    class Timezone(datetime.tzinfo, metaclass=CallbackType):
        pass

    payload = {
        "value": [Value()],
        "key": {Value(): 1},
        "timezone": datetime.datetime(2026, 9, 27, tzinfo=Timezone()),
    }[position]
    calls.clear()
    assert not _plain_data(payload)
    assert calls == []


def test_render_method_binds_each_response_without_retaining_finished_responses():
    responses = [Response({"id": i}) for i in range(2)]
    for response in responses:
        response.accepted_renderer = JSONRenderer()
        response.accepted_media_type = "application/json"
        response.renderer_context = {}
    first, second = [response.render for response in responses]
    assert iscoroutinefunction(first)
    assert iscoroutinefunction(second)
    responses[0].data["id"] = 10
    assert first().content == b'{"id":10}'
    assert second().content == b'{"id":1}'
    assert first() is responses[0]
    reference = weakref.ref(responses[0])
    del responses, first
    gc.collect()
    assert reference() is None


async def test_an_ordered_dict_renders_in_its_own_order_on_the_loop():
    # Paginators written before Python 3.7 (drf-tweaks) still return one.
    page = OrderedDict([("results", [1]), ("count", 1), ("next", None)])
    page.move_to_end("results")  # its order is no longer the dict's
    response = Response(page)
    response.accepted_renderer = JSONRenderer()
    response.accepted_media_type = "application/json"
    response.renderer_context = {}
    with count_hops() as hops:
        await response.render()
    assert hops.count == 0
    assert response.content == b'{"count":1,"next":null,"results":[1]}'


@pytest.mark.parametrize("renderer_class", [JSONRenderer, MsgspecJSONRenderer])
async def test_payload_checked_renderers_evaluate_unknown_values_only_in_worker(
    renderer_class,
):
    # msgspec's encoder hook turns any iterable (a QuerySet) into a list and
    # evaluates lazy strings, so it gets the same payload check as DRF's.
    evaluated = []

    def value():
        evaluated.append(threading.get_ident())
        return "resolved"

    response = Response({"items": [lazy(value, str)()]})
    response.accepted_renderer = renderer_class()
    response.accepted_media_type = "application/json"
    response.renderer_context = {}
    with count_hops() as hops:
        await response.render()
    assert hops.count == 1
    assert evaluated[0] != threading.get_ident()
    assert response.content == b'{"items":["resolved"]}'


async def test_msgspec_renderer_renders_plain_data_on_the_loop():
    response = Response({"items": [1, "two"]})
    response.accepted_renderer = MsgspecJSONRenderer()
    response.accepted_media_type = "application/json"
    response.renderer_context = {}
    with count_hops() as hops:
        await response.render()
    assert hops.count == 0
    assert response.content == b'{"items":[1,"two"]}'


async def test_streamed_items_get_the_payload_check_of_their_renderer():
    from aiodrf.response import StreamingResponse

    evaluated = []

    def value():
        evaluated.append(threading.get_ident())
        return "resolved"

    async def items():
        yield {"plain": 1}
        yield {"lazy": lazy(value, str)()}

    response = StreamingResponse(items(), renderer=MsgspecJSONRenderer())
    with count_hops() as hops:
        chunks = [chunk async for chunk in response]
    assert b"".join(chunks) == b'{"plain":1}\n{"lazy":"resolved"}\n'
    assert hops.count == 1
    assert evaluated[0] != threading.get_ident()


class SecondsEncoder(DRFEncoder):
    def default(self, o):
        if isinstance(o, datetime.timedelta):
            return f"PT{int(o.total_seconds())}S"
        return super().default(o)


def _drf_rendered(renderer_class, data):
    return renderer_class().render(data, "application/json", {})


def test_the_kept_renderers_follow_their_classes_as_they_are(monkeypatch):
    from aiodrf.response import _KEPT_ENCODER_RENDERERS

    data = {"price": datetime.timedelta(seconds=90), "text": "ş"}
    # What a project may set in ``AppConfig.ready``, after aiodrf is imported.
    monkeypatch.setattr(JSONRenderer, "encoder_class", SecondsEncoder)
    monkeypatch.setattr(JSONRenderer, "ensure_ascii", True)
    kept = _KEPT_ENCODER_RENDERERS[JSONRenderer]
    assert kept.render(data, "application/json", {}) == _drf_rendered(
        JSONRenderer, data
    )

    def shouting(self, data, accepted_media_type=None, renderer_context=None):
        return b"SHOUT"

    monkeypatch.setattr(JSONRenderer, "render", shouting)
    assert kept.render(data, "application/json", {}) == b"SHOUT"

    monkeypatch.setattr(MsgspecJSONRenderer, "render", shouting)
    kept = _KEPT_ENCODER_RENDERERS[MsgspecJSONRenderer]
    assert kept.render({"a": 1}, "application/json", {}) == b"SHOUT"


class Tricky:
    pass


@pytest.mark.parametrize(
    "rows",
    [
        [{"id": index, "name": f"n{index}"} for index in range(10)],
        [{"id": 1, "when": datetime.datetime(2026, 9, 29, tzinfo=datetime.UTC)}],
    ],
)
def test_drfs_returned_containers_are_checked_as_their_builtin_counterparts(
    monkeypatch, rows
):
    from aiodrf import response as module

    calls = []
    real = module._builtins_only
    monkeypatch.setattr(
        module, "_builtins_only", lambda value: calls.append(1) or real(value)
    )
    returned = ReturnList(rows, serializer=None)
    assert _plain_data(returned)
    assert _plain_data({"count": 10, "results": returned})
    assert _plain_data(ReturnDict(rows[0], serializer=None))
    # The whole list at once when it holds built-in values only.
    if "when" not in rows[0]:
        assert len(calls) <= 6
    assert not _plain_data(ReturnList([{"x": Tricky()}], serializer=None))
    assert not _plain_data(ReturnDict({"x": Tricky()}, serializer=None))


class OpinionatedEncoder(DRFEncoder):
    # Defaults of its own, which DRF's explicit json.dumps arguments override.
    def __init__(self, *args, indent=2, sort_keys=True, **kwargs):
        super().__init__(*args, indent=indent, sort_keys=sort_keys, **kwargs)


def test_the_kept_encoders_take_json_dumps_arguments(monkeypatch):
    from aiodrf.contrib import monkeypatches
    from aiodrf.response import _KEPT_ENCODER_RENDERERS

    monkeypatch.setattr(JSONRenderer, "encoder_class", OpinionatedEncoder)
    data = {"b": 1, "a": 2}
    expected = JSONRenderer().render(data, "application/json", {})
    assert expected == b'{"b":1,"a":2}'
    kept = _KEPT_ENCODER_RENDERERS[JSONRenderer]
    assert kept.render(data, "application/json", {}) == expected
    monkeypatches.apply("keep_json_encoders")
    try:
        assert JSONRenderer().render(data, "application/json", {}) == expected
    finally:
        monkeypatches.revert("keep_json_encoders")
