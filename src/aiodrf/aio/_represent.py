"""Representation: ``data``, ``to_representation`` and the compiled path."""

import inspect
from typing import Any

from django.db import models
from fastdrf._compiled import _declares_backend, _producer
from fastdrf.settings import fastdrf_settings
from rest_framework import fields, serializers
from rest_framework.fields import SkipField
from rest_framework.relations import PKOnlyObject

from aiodrf.aio._classify import (
    _field_repr_is_async,
    _has_async_repr_override,
    has_async_representation,
    is_static,
)
from aiodrf.aio._common import NEEDS_AWAIT, _acall, _sync_member
from aiodrf.aio._loaded import reads_loaded
from aiodrf.settings import aiodrf_settings
from aiodrf.utils import (
    Impl,
    awaits_inline,
    definer,
    is_bridge_base,
    maybe_await,
    resolve_pair,
    run_sync,
    run_sync_and_await,
)


async def data(serializer: Any) -> Any:
    """Async counterpart of ``serializer.data`` for any DRF serializer."""
    if resolve_pair(serializer, "data", "adata") is Impl.ASYNC:
        return await _acall(serializer.adata)
    return await default_data(serializer)


async def default_data(serializer: Any) -> Any:
    """The default operation, also used by async overrides calling super()."""
    if hasattr(serializer, "initial_data") and not hasattr(
        serializer, "_validated_data"
    ):
        # Let DRF raise its explanatory AssertionError.
        return await _cached_data(serializer)
    if hasattr(serializer, "_data"):
        return await _cached_data(serializer)

    if serializer.instance is not None and not getattr(serializer, "_errors", None):
        source = serializer.instance
    elif hasattr(serializer, "_validated_data") and not getattr(
        serializer, "_errors", None
    ):
        source = serializer.validated_data
    elif aiodrf_settings.REPRESENTATION_MODE == "inline":
        return serializer.data
    else:
        # DRF's get_initial(): the fields' initial values, callables included,
        # or the project's override.
        return await run_sync(_read_data)(serializer)

    if serializer.__dict__.get("_aiodrf_async_representation"):
        # Classified already, by the worker of a generic action.
        result = NEEDS_AWAIT
    elif aiodrf_settings.REPRESENTATION_MODE == "inline":
        # The project asserts that representing loaded objects does no I/O.
        # A query made here raises; it is not repeated in a thread.
        if _is_lazy(source):
            result = (
                NEEDS_AWAIT
                if has_async_representation(serializer)
                else await run_sync(try_data)(serializer)
            )
        else:
            # try_data already classifies async overrides, including compiler
            # fallback. Loaded values need no separate classification here.
            result = try_data(serializer)
    else:
        produce = _loop_data(serializer, source)
        if produce is None and reads_loaded(serializer, source):
            # Everything it reads is loaded: DRF's representation cannot query.
            produce = try_data
        result = (
            produce(serializer) if produce else await run_sync(try_data)(serializer)
        )
    if result is not NEEDS_AWAIT:
        return result
    serializer._data = await to_representation(serializer, source)
    return await _cached_data(serializer)


async def _cached_data(serializer: Any) -> Any:
    # A cached representation does not make a custom data property nonblocking.
    if aiodrf_settings.REPRESENTATION_MODE == "inline" or is_bridge_base(
        definer(type(serializer), "data")
    ):
        return serializer.data
    return await run_sync(_read_data)(serializer)


def _read_data(serializer: Any) -> Any:
    return serializer.data


def _loop_data(serializer: Any, source: Any) -> Any:
    """
    The compiled producer of ``serializer.data`` when it provably makes no
    query, so that it runs on the event loop in thread mode too, else None:
    a model instance whose class compiled to an encoder that reads only
    columns the instance has loaded (:func:`fastdrf.compiler.loaded_encoder`).
    """
    if (
        not isinstance(source, models.Model)
        or not _uses_compiler(serializer)
        or not is_bridge_base(definer(type(serializer), "data"))
    ):
        return None
    from fastdrf.compiler import loaded_encoder

    # ``source`` is ``serializer.instance``, which the encoder reads.
    encoder = loaded_encoder(serializer)
    return None if encoder is None else _producer(serializer, encoder, source)


def try_data(serializer: Any) -> Any:
    """
    The synchronous half of :func:`data`: ``serializer.data``, produced by
    the compiled backend when one applies, or :data:`NEEDS_AWAIT`.
    """
    produce = _compiled_data(serializer)
    if produce is NEEDS_AWAIT:
        return NEEDS_AWAIT
    return (produce or _sync_data)(serializer)


def _sync_data(serializer: Any) -> Any:
    return serializer.data


def _compiled_data(serializer: Any) -> Any:
    """
    ``serializer.data`` produced by a compiled msgspec/pydantic class, when
    ``SERIALIZER_BACKEND`` (or ``Meta.serializer_backend``) asks for one and
    the serializer compiles (see :mod:`fastdrf.compiler`), None for
    DRF's code, or :data:`NEEDS_AWAIT`.
    """
    source = serializer.instance
    if (
        not _uses_compiler(serializer)
        or source is None
        or getattr(serializer, "_errors", None)
        or hasattr(serializer, "_data")
    ):
        return NEEDS_AWAIT if has_async_representation(serializer) else None
    from fastdrf.compiler import compiled_for, declines_source

    # A static serializer is looked up by its class before it is classified:
    # classifying it the first time builds its fields, and an instance whose
    # fields exist is compiled by its signature instead. A list's own awaited
    # representation (``PrefetchListSerializer.aprefetch``) runs whatever
    # its child compiles to: ``is_static`` speaks for the child only.
    if (
        isinstance(serializer, serializers.ListSerializer)
        and _has_async_repr_override(serializer)
    ) or (not is_static(serializer) and has_async_representation(serializer)):
        return NEEDS_AWAIT
    encoder = compiled_for(serializer)
    if encoder is None:
        return NEEDS_AWAIT if has_async_representation(serializer) else None

    if not isinstance(serializer, serializers.ListSerializer) and declines_source(
        serializer, source
    ):
        return None
    return _producer(serializer, encoder, source)


def _uses_compiler(serializer: Any) -> bool:
    return fastdrf_settings.SERIALIZER_BACKEND != "drf" or _declares_backend(serializer)


def _is_lazy(source: Any) -> bool:
    """Unevaluated querysets and related managers query when iterated."""
    if isinstance(source, models.manager.BaseManager):
        return True
    return isinstance(source, models.QuerySet) and source._result_cache is None


async def _represent_where_configured(func: Any, /, *args: Any) -> Any:
    """Run synchronous representation code where ``REPRESENTATION_MODE`` says."""
    if aiodrf_settings.REPRESENTATION_MODE == "inline":
        return func(*args)
    return await run_sync(func)(*args)


async def to_representation(serializer: Any, instance: Any) -> Any:
    """Async counterpart of ``serializer.to_representation()``."""
    impl = resolve_pair(serializer, "to_representation", "ato_representation")
    if impl is Impl.ASYNC:
        return await _acall(serializer.ato_representation, instance)
    if impl is Impl.SYNC_IS_ASYNC:
        return await _acall(serializer.to_representation, instance)
    if impl is Impl.SYNC:
        # A custom sync ``to_representation`` owns the whole output.
        return await _represent_where_configured(serializer.to_representation, instance)
    return await default_to_representation(serializer, instance)


async def default_to_representation(serializer: Any, instance: Any) -> Any:
    """The implementation behind :func:`to_representation` and ``ato_representation()``."""
    asynchronous = serializer.__dict__.get("_aiodrf_async_representation")
    if (
        asynchronous is None
        or _has_async_repr_override(serializer)
        or (
            isinstance(serializer, serializers.Serializer)
            and "fields" not in vars(serializer)
        )
    ):
        # Direct callers and async overrides calling super() need the same
        # worker boundary as .data. Override classification alone builds no
        # fields; a per-class cache hit need not build them either.
        asynchronous = await _represent_where_configured(
            _prepare_representation, serializer
        )
    if not asynchronous:
        method = _sync_member(serializer, "to_representation", "ato_representation")
        return await _represent_where_configured(method, instance)

    if isinstance(serializer, serializers.ListSerializer):
        items = await _alist(instance)
        return [await to_representation(serializer.child, item) for item in items]

    # Fields are represented in DRF's order. Consecutive synchronous fields
    # go together (one hop in thread mode); async ones are awaited in between.
    model = getattr(getattr(serializer, "Meta", None), "model", None)
    ret = {}
    pending = []
    for field in serializer._readable_fields:
        if not _field_repr_is_async(serializer, field, model):
            pending.append(field)
            continue
        if pending:
            ret.update(
                await _represent_where_configured(_represent_fields, pending, instance)
            )
            pending = []
        try:
            ret[field.field_name] = await _afield_representation(field, instance)
        except SkipField:
            continue
    if pending:
        ret.update(
            await _represent_where_configured(_represent_fields, pending, instance)
        )
    return ret


def _prepare_representation(serializer: Any) -> bool:
    asynchronous = has_async_representation(serializer)
    if asynchronous and isinstance(serializer, serializers.Serializer):
        model = getattr(getattr(serializer, "Meta", None), "model", None)
        for field in serializer.fields.values():
            if not field.write_only:
                _field_repr_is_async(serializer, field, model)
    return asynchronous


def _represent_fields(fields_: Any, instance: Any) -> dict[str, Any]:
    """The body of the loop in DRF's ``Serializer.to_representation``."""
    ret = {}
    for field in fields_:
        try:
            attribute = field.get_attribute(instance)
        except SkipField:
            continue
        ret[field.field_name] = _represent(field, attribute)
    return ret


async def _afield_representation(field: Any, instance: Any) -> Any:
    # Async model methods and properties used as ``source`` return an
    # awaitable from ``get_attribute``.
    if (
        type(field) is fields.SerializerMethodField
        and type(field.source_attrs) is list
        and not field.source_attrs
        and "get_attribute" not in vars(field)
    ):
        # DRF's source='*' returns the instance. Do not send that
        # identity lookup to a worker once per async method field per row.
        # Custom fields and instance overrides keep the configured boundary.
        attribute = field.get_attribute(instance)
    else:
        attribute = await _represent_where_configured(field.get_attribute, instance)
    attribute = await maybe_await(attribute)
    if isinstance(attribute, PKOnlyObject) and inspect.isawaitable(attribute.pk):
        # DRF's ``RelatedField.get_attribute`` for an async source: the
        # relation's object (or its pk), once awaited.
        value = await attribute.pk
        attribute = PKOnlyObject(pk=getattr(value, "pk", value))
    if getattr(field, "_aiodrf_async_field", False):
        # adrf's ``async def ato_representation`` (aiodrf.contrib.adrf_compat).
        check_for_none = (
            attribute.pk if isinstance(attribute, PKOnlyObject) else attribute
        )
        if check_for_none is None:
            return None
        return await _acall(field.ato_representation, attribute)
    if isinstance(field, serializers.BaseSerializer):
        if attribute is None:
            return None
        return await to_representation(field, attribute)
    if isinstance(field, fields.SerializerMethodField):
        # DRF calls ``get_<field>``, whose coroutine is awaited here; a
        # synchronous wrapper of one is called in the worker.
        method = getattr(field.parent, field.method_name)
        if not awaits_inline(method):
            return await run_sync_and_await(_represent, field, attribute)
        return await maybe_await(_represent(field, attribute))
    if (
        type(field) in (fields.CharField, fields.IntegerField, fields.FloatField)
        and "to_representation" not in vars(field)
        and type(attribute) in (str, int, float, bool)
    ):
        # These exact converters only call str/int/float on a built-in value.
        return _represent(field, attribute)
    # Conversion may call a project hook, even on a built-in field instance.
    return await _represent_where_configured(_represent, field, attribute)


def _represent(field: Any, attribute: Any) -> Any:
    # Same None handling as ``Serializer.to_representation``.
    check_for_none = attribute.pk if isinstance(attribute, PKOnlyObject) else attribute
    if check_for_none is None:
        return None
    return field.to_representation(attribute)


async def _alist(iterable: Any) -> list[Any]:
    if type(iterable) in (list, tuple):
        return list(iterable)
    return await run_sync(_materialize)(iterable)


def _materialize(iterable: Any) -> list[Any]:
    if isinstance(iterable, models.manager.BaseManager):
        iterable = iterable.all()
    return list(iterable)
