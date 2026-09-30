"""Opt-in patches of DRF (``aiodrf.contrib.monkeypatches``)."""

import asyncio
import gc
import weakref

import pytest
from django.apps import apps
from django.core.asgi import get_asgi_application
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
from django.test.utils import isolate_apps
from django.urls import path
from rest_framework import response as drf_response
from rest_framework import serializers as drf
from rest_framework import views as drf_views
from rest_framework.renderers import JSONRenderer
from rest_framework.utils.encoders import JSONEncoder as DRFJSONEncoder

from aiodrf.contrib import monkeypatches
from aiodrf.settings import ASGIREF_VERSION, setting_error
from tests.asgi_driver import http_scope


@pytest.fixture
def patched():
    """Apply patches for one test and undo them after it."""
    applied = []

    def apply(*names):
        monkeypatches.apply(*names)
        applied.extend(names)

    yield apply
    monkeypatches.revert(*applied)


class Row(drf.Serializer):
    name = drf.CharField()


def test_revert_restores_drfs_own_members(patched):
    # DRF's ``Response`` has no ``close`` of its own: Django's is inherited.
    missing = object()
    originals = {
        name: owner.__dict__.get(attribute, missing)
        for name, (owner, attribute) in monkeypatches.TARGETS.items()
    }
    assert originals["release_drf_responses"] is missing
    patched(*monkeypatches.PATCHES)
    patched_members = {
        name: owner.__dict__[attribute]
        for name, (owner, attribute) in monkeypatches.TARGETS.items()
    }
    monkeypatches.apply(*monkeypatches.PATCHES)  # applying twice patches once
    for name, (owner, attribute) in monkeypatches.TARGETS.items():
        assert owner.__dict__[attribute] is patched_members[name]
        assert patched_members[name] is not originals[name]
    monkeypatches.revert(*monkeypatches.PATCHES)
    for name, (owner, attribute) in monkeypatches.TARGETS.items():
        assert owner.__dict__.get(attribute, missing) is originals[name]


def test_an_unknown_patch_is_refused():
    with pytest.raises(ImproperlyConfigured, match="unknown"):
        monkeypatches.apply("unknown")
    assert "unknown" in setting_error("MONKEYPATCHES", ["unknown"])
    assert setting_error("MONKEYPATCHES", "weak_list_children")
    assert setting_error("MONKEYPATCHES", list(monkeypatches.PATCHES)) is None


def test_the_setting_applies_the_patches_at_startup():
    with override_settings(AIODRF={"MONKEYPATCHES": ["weak_list_children"]}):
        try:
            apps.get_app_config("aiodrf").ready()
            assert monkeypatches.applied() == ["weak_list_children"]
        finally:
            monkeypatches.revert("weak_list_children")
    assert monkeypatches.applied() == []


def test_weak_list_children_covers_drfs_own_list_serializers(patched):
    patched("weak_list_children")
    serializer = Row([{"name": "a"}], many=True)
    assert serializer.child.parent == serializer
    assert serializer.data == [{"name": "a"}]
    reference = weakref.ref(serializer)
    gc.collect()
    gc.disable()
    try:
        del serializer
        assert reference() is None
    finally:
        gc.enable()


views = []


class DRFView(drf_views.APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        views.append(weakref.ref(self))
        return drf_response.Response({"ok": True})


urlpatterns = [path("drf/", DRFView.as_view())]


async def _serve(application, url):
    sent = []
    received = False

    async def receive():
        nonlocal received
        if received:
            await asyncio.Event().wait()
        received = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await application(http_scope(url), receive, send)
    return sent[0]["status"]


@pytest.mark.skipif(
    ASGIREF_VERSION < (3, 9), reason="asgiref < 3.9 keeps request frames in a cycle"
)
@pytest.mark.django_db(transaction=True)
@override_settings(ROOT_URLCONF=__name__)
async def test_release_drf_responses_frees_a_drf_views_request_objects(patched):
    patched("release_drf_responses")
    application = get_asgi_application()
    assert await _serve(application, "/drf/") == 200
    views.clear()
    gc.collect()
    gc.disable()
    try:
        assert await _serve(application, "/drf/") == 200
        assert views[0]() is None
    finally:
        gc.enable()


# -- cache_model_field_info -------------------------------------------------------


def _info(model):
    from rest_framework.utils import model_meta

    info = model_meta.get_field_info(model)
    return info.pk, *(dict(part) for part in info[1:])


def test_model_field_info_is_computed_once_per_model(patched):
    from rest_framework.utils import model_meta

    from tests.testapp.models import Author, Book, Edition

    expected = {model: _info(model) for model in (Author, Book, Edition)}
    patched("cache_model_field_info")
    for model, info in expected.items():
        assert _info(model) == info
        assert model_meta.get_field_info(model) is model_meta.get_field_info(model)
    # DRF's ``update()`` passes the instance, which may have no key yet.
    assert model_meta.get_field_info(Book()) is model_meta.get_field_info(Book)


def test_model_field_info_follows_djangos_meta_cache(patched):
    from rest_framework.utils import model_meta

    from tests.testapp.models import Book

    patched("cache_model_field_info")
    before = model_meta.get_field_info(Book)
    # What Django does when a model's fields change (``apps.clear_cache``).
    Book._meta._expire_cache()
    after = model_meta.get_field_info(Book)
    assert after is not before
    assert _info(Book) == (before.pk, *(dict(part) for part in before[1:]))


def test_serializers_build_the_same_fields_with_the_cached_info(patched):
    from tests.testapp.models import Book

    class Books(drf.ModelSerializer):
        class Meta:
            model = Book
            fields = "__all__"

    expected = repr(Books())
    patched("cache_model_field_info")
    assert repr(Books()) == expected


# -- keep_json_encoders -------------------------------------------------------------


class SpacedJSONRenderer(JSONRenderer):
    compact = False
    ensure_ascii = True


class TaggedEncoder(DRFJSONEncoder):
    def default(self, o):
        if isinstance(o, set):
            return sorted(o)
        return super().default(o)


class TaggedJSONRenderer(JSONRenderer):
    encoder_class = TaggedEncoder


class OwnIndentJSONRenderer(JSONRenderer):
    def get_indent(self, accepted_media_type, renderer_context):
        return 3


PAYLOAD = {"text": "ş\u2028\u2029", "n": 1.5, "tags": {2, 1}, "none": None}


@pytest.mark.parametrize(
    "renderer_class",
    [JSONRenderer, SpacedJSONRenderer, TaggedJSONRenderer, OwnIndentJSONRenderer],
)
@pytest.mark.parametrize(
    ("accept", "context"),
    [
        ("application/json", {}),
        ("application/json", {"indent": 2}),
        ("application/json; indent=4", {}),
        (None, None),
    ],
)
def test_kept_json_encoders_render_drfs_bytes(patched, renderer_class, accept, context):
    data = (
        PAYLOAD
        if renderer_class is TaggedJSONRenderer
        else {k: v for k, v in PAYLOAD.items() if k != "tags"}
    )
    expected = renderer_class().render(data, accept, context)
    patched("keep_json_encoders")
    assert renderer_class().render(data, accept, context) == expected
    assert renderer_class().render(None, accept, context) == b""


def test_a_renderer_changed_on_the_instance_keeps_drfs_path(patched):
    patched("keep_json_encoders")
    renderer = JSONRenderer()
    renderer.ensure_ascii = True
    assert renderer.render({"a": "ş"}) == b'{"a":"\\u015f"}'


def test_the_kept_encoder_is_built_once_per_class(patched, monkeypatch):
    patched("keep_json_encoders")
    JSONRenderer().render({"a": 1})
    built = []
    real = DRFJSONEncoder.__init__

    def counting(self, *args, **kwargs):
        built.append(1)
        real(self, *args, **kwargs)

    monkeypatch.setattr(TaggedEncoder, "__init__", counting)
    for _ in range(3):
        TaggedJSONRenderer().render({"tags": {1}})
    assert len(built) == 1


@isolate_apps("tests.testapp")
def test_model_field_info_of_a_proxy_follows_its_concrete_model(patched):
    from django.db import models
    from rest_framework.utils import model_meta

    class Concrete(models.Model):
        name = models.CharField(max_length=10)

        class Meta:
            app_label = "testapp"

        def __str__(self):
            return self.name

    class Proxy(Concrete):
        class Meta:
            proxy = True
            app_label = "testapp"

        def __str__(self):
            return self.name

    patched("cache_model_field_info")
    assert list(model_meta.get_field_info(Proxy).fields) == ["name"]
    models.IntegerField(default=0).contribute_to_class(Concrete, "added")
    assert list(model_meta.get_field_info(Proxy).fields) == ["name", "added"]
