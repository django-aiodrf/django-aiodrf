"""Diagnostic scopes and process caches must have explicit retention bounds."""

import asyncio
import contextvars
import gc
import weakref

import pytest
from django.test import override_settings

from aiodrf import serializers, utils
from aiodrf.contrib.builtin import field_cache
from tests.testapp.models import Author


async def test_child_task_stops_recording_when_hop_scope_exits():
    ready = asyncio.Event()

    async def child():
        await ready.wait()
        await utils.run_sync(lambda: None)()

    with utils.count_hops() as hops:
        task = asyncio.create_task(child())
    ready.set()
    await task
    assert hops.calls == []


def test_copied_context_does_not_own_a_finished_hop_counter():
    with utils.count_hops() as hops:
        context = contextvars.copy_context()
        reference = weakref.ref(hops)
    del hops
    gc.collect()
    assert reference() is None
    # Retain the copied context until after the reachability assertion.
    assert context is not None


async def test_detached_task_can_outlive_and_release_the_counter():
    ready = asyncio.Event()

    async def child():
        await ready.wait()
        await utils.run_sync(lambda: None)()

    with utils.count_hops() as hops:
        reference = weakref.ref(hops)
        task = asyncio.create_task(child())
    del hops
    try:
        gc.collect()
        assert reference() is None
    finally:
        ready.set()
        await task


async def test_hop_scopes_restore_the_outer_counter_after_an_exception():
    def outer():
        pass

    def inner():
        raise ValueError("scope")

    with utils.count_hops() as first:
        await utils.run_sync(outer)()
        with pytest.raises(ValueError, match="scope"), utils.count_hops() as second:
            await utils.run_sync(inner)()
        await utils.run_sync(outer)()
    assert first.calls == [outer.__qualname__] * 2
    assert second.calls == [inner.__qualname__]


@pytest.mark.parametrize(
    "register",
    [
        utils.async_safe,
        utils.register_pure,
        lambda cls: utils.register_pure(cls, leaf=True),
        lambda cls: utils.register_pure_method(cls, "check"),
        lambda cls: utils.register_pure_method(cls, "check", leaf=True),
        utils.bridge_base,
        utils._transparent,
    ],
)
def test_class_registrations_do_not_own_temporary_classes(register):
    def build():
        class Temporary:
            def check(self):
                return True

        register(Temporary)
        utils.is_pure(Temporary, "check")
        utils.resolve_pair(Temporary, "check", "acheck")
        return weakref.ref(Temporary)

    reference = build()
    gc.collect()
    assert reference() is None


def test_class_cache_bounds_values_that_refer_back_to_the_key(monkeypatch):
    monkeypatch.setattr(utils, "CLASS_CACHE_SIZE", 8)

    @utils.class_cache
    def identity(cls, variant):
        return cls

    references = []
    for index in range(24):
        cls = type(f"Temporary{index}", (), {})
        references.append(weakref.ref(cls))
        assert identity(cls, index) is cls
        assert identity.cache_size() <= 8
    del cls
    gc.collect()
    assert all(reference() is None for reference in references[:-8])
    identity.cache_clear()
    gc.collect()
    assert all(reference() is None for reference in references)


@pytest.mark.aiodrf_settings(CACHE_SERIALIZER_FIELDS=False, FIELD_COPY_MODE="deepcopy")
@pytest.mark.parametrize("backend", ["msgspec", "pydantic"])
def test_input_signatures_do_not_retain_rejected_callable_limits(backend):
    from django.core.validators import MaxValueValidator

    from aiodrf.contrib import inputs

    def build():
        class Temporary(serializers.Serializer):
            value = serializers.IntegerField()

        def limit():
            return Temporary

        Temporary._declared_fields["value"]._kwargs["validators"] = [
            MaxValueValidator(limit)
        ]
        assert (
            inputs.recognize(Temporary(data={"value": 1}), backend=backend)
            is inputs.NOT_RECOGNIZED
        )
        return weakref.ref(Temporary)

    reference = build()
    gc.collect()
    assert reference() is None


@pytest.mark.parametrize("limit", [[], {}, lambda: 1])
def test_unsupported_validator_limits_do_not_need_to_be_hashable(limit):
    from django.core.validators import MaxValueValidator

    from aiodrf.contrib import inputs

    class Input(serializers.Serializer):
        value = serializers.IntegerField(validators=[MaxValueValidator(limit)])

    assert inputs.recognize(Input(data={"value": 1})) is inputs.NOT_RECOGNIZED


@pytest.mark.parametrize("direction", ["input", "output"])
def test_compiler_buckets_bound_custom_field_class_cycles(monkeypatch, direction):
    from aiodrf.contrib import compiler, inputs

    monkeypatch.setattr(compiler, "MAX_SERIALIZER_CLASSES", 8)

    def build():
        class Temporary(serializers.Serializer):
            pass

        class CustomField(serializers.IntegerField):
            def to_representation(self, value):
                assert Temporary is not None
                return super().to_representation(value)

        Temporary._declared_fields["value"] = CustomField()
        instance = Temporary(data={"value": 1})
        if direction == "input":
            assert inputs.recognize(instance) is inputs.NOT_RECOGNIZED
        else:
            assert compiler.compiled_for(instance) is None
        return weakref.ref(Temporary)

    with override_settings(AIODRF={"SERIALIZER_BACKEND": "msgspec"}):
        references = [build() for _ in range(24)]
        gc.collect()
        assert all(reference() is None for reference in references[:-8])
    # Both compilers invalidate formats on REST_FRAMEWORK, not AIODRF.
    inputs.clear_recognizers(setting="REST_FRAMEWORK")
    compiler.clear_compiled(setting="REST_FRAMEWORK")
    gc.collect()
    assert all(reference() is None for reference in references)


def test_cleared_compiler_bucket_cannot_be_republished():
    from aiodrf.contrib.compiler import _SerializerCache

    cache = _SerializerCache()
    previous = cache.get_or_create(Author)
    cache.clear()
    previous["old"] = True
    assert cache.get(Author) is None
    assert cache.get_or_create(Author) == {}


@pytest.mark.parametrize("size", [0, -1, 1.5, None])
def test_schema_cache_rejects_sizes_that_cannot_bound_eviction(size):
    from aiodrf.contrib.typed import BoundedCache

    with pytest.raises(ValueError, match="positive integer"):
        BoundedCache(size)


def test_prefetch_cache_hits_do_not_acquire_the_publication_lock(monkeypatch):
    from aiodrf.contrib.builtin import prefetch

    monkeypatch.setattr(prefetch, "_static_tree", lambda serializer: True)
    monkeypatch.setattr(
        prefetch, "related_lookups", lambda *args, **kwargs: ([], ["children"])
    )
    cache = prefetch._LookupCache()
    cache.get(type, object, object())

    class UnexpectedLock:
        def __enter__(self):
            pytest.fail("A warm lookup must not acquire the publication lock")

        def __exit__(self, *args):
            pass

    cache._lock = UnexpectedLock()
    assert cache.get(type, object, object()) == ([], ["children"])


def test_prefetch_clear_does_not_publish_an_inflight_lookup(monkeypatch):
    from aiodrf.contrib.builtin import prefetch

    cache = prefetch._LookupCache()
    monkeypatch.setattr(prefetch, "_static_tree", lambda serializer: True)

    def inspect(*args, **kwargs):
        cache.clear()
        return [], ["children"]

    monkeypatch.setattr(prefetch, "related_lookups", inspect)
    assert cache.get(type, object, object()) == ([], ["children"])
    assert not cache._entries


def test_prefetch_cache_does_not_own_serializer_or_model_classes(monkeypatch):
    from aiodrf.contrib.builtin import prefetch

    monkeypatch.setattr(prefetch, "_static_tree", lambda serializer: True)
    monkeypatch.setattr(
        prefetch, "related_lookups", lambda *args, **kwargs: ([], ["children"])
    )
    cache = prefetch._LookupCache()

    class TemporaryModel:
        pass

    class TemporarySerializer:
        pass

    model = weakref.ref(TemporaryModel)
    serializer = weakref.ref(TemporarySerializer)
    assert cache.get(TemporarySerializer, TemporaryModel, object()) == (
        [],
        ["children"],
    )
    del TemporaryModel
    gc.collect()
    assert model() is None
    del TemporarySerializer
    gc.collect()
    assert serializer() is None


async def test_cancellation_closes_the_hop_scope_for_inherited_contexts():
    with utils.count_hops() as hops:
        copied = contextvars.copy_context()

    async def cancelled_scope():
        with utils.count_hops() as inner:
            snapshot = contextvars.copy_context()
            try:
                raise asyncio.CancelledError
            finally:
                retained.append((inner, snapshot))

    retained = []
    with pytest.raises(asyncio.CancelledError):
        await cancelled_scope()
    counter, context = retained.pop()

    def record():
        return utils.run_sync(lambda: None)()

    await asyncio.create_task(record(), context=context)
    await asyncio.create_task(record(), context=copied)
    assert counter.count == hops.count == 0


def test_class_cache_bounds_variants_of_one_live_class(monkeypatch):
    monkeypatch.setattr(utils, "CLASS_CACHE_SIZE", 8)

    @utils.class_cache
    def metadata(cls, variant):
        return variant

    for index in range(32):
        assert metadata(Author, index) == index
        assert metadata.cache_size() <= 8


@pytest.mark.parametrize("mode", ["deepcopy", "clone", "compiled"])
def test_field_templates_bound_validator_backreferences(monkeypatch, mode):
    monkeypatch.setattr(utils, "CLASS_CACHE_SIZE", 8)

    def build():
        class Temporary(serializers.ModelSerializer):
            class Meta:
                model = Author
                fields = ["name"]

        def validate(value):
            # Application validators can close over the serializer class.
            assert Temporary is not None
            return value

        Temporary._declared_fields["name"] = serializers.CharField(
            validators=[validate]
        )
        assert "name" in Temporary().fields
        return weakref.ref(Temporary)

    with override_settings(
        AIODRF={"CACHE_SERIALIZER_FIELDS": True, "FIELD_COPY_MODE": mode}
    ):
        references = [build() for _ in range(24)]
        gc.collect()
        assert all(reference() is None for reference in references[:-8])
        assert field_cache._field_template.cache_size() <= 8
    gc.collect()
    assert all(reference() is None for reference in references)


def test_a_class_cache_forgets_a_class_that_goes_away():
    from aiodrf.utils import class_cache

    @class_cache
    def name_of(cls):
        return cls.__name__

    gone = type("Gone", (), {})
    assert name_of(gone) == "Gone"
    assert name_of.cache_size() == 1
    reference = weakref.ref(gone)
    del gone
    gc.collect()
    assert reference() is None
    assert name_of.cache_size() == 0
    # Another class, perhaps at the same address, is not answered for it.
    assert name_of(type("New", (), {})) == "New"
