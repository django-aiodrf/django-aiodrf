"""
DRF serializers with an async API.

Every class adds ``ais_valid()``, ``adata()``, ``asave()``, ``acreate()`` and
``aupdate()`` to its DRF base. The async hooks users may define are:

* ``async def validate_<field>(self, value)`` (or ``avalidate_<field>``),
* ``async def validate(self, attrs)`` (or ``avalidate``),
* async validators (functions or classes with ``async def __call__``),
* ``async def get_<field>(self, obj)`` for ``SerializerMethodField``,
* async model methods and properties used as a field ``source``,
* ``async def acreate`` / ``aupdate``.

Fields and relations are DRF's own classes; aiodrf does not replace them.
Plain DRF serializers work in aiodrf views as well (see :mod:`aiodrf.aio`),
so this module is only needed for the async hooks above.
"""

import types
from collections.abc import Coroutine, Generator
from typing import TYPE_CHECKING, Any

from django.db.models import Model
from django.db.models.manager import BaseManager
from fastdrf._field_cache import _model_serializer_fields, _serializer_fields
from rest_framework import serializers
from rest_framework.fields import empty

# Keyword arguments that go to the ``ListSerializer`` but not to the child.
from rest_framework.serializers import (
    LIST_SERIALIZER_KWARGS,
    LIST_SERIALIZER_KWARGS_REMOVE,
)

from aiodrf import aio
from aiodrf.aio._classify import (
    Kind,
    has_async_representation,
    validation_kind,
)
from aiodrf.aio._represent import default_data
from aiodrf.aio._save import _atomic
from aiodrf.compat import BigIntegerField
from aiodrf.utils import (
    Impl,
    bridge_base,
    bridge_to_async,
    bridges_to,
    class_cache,
    resolve_pair,
    user_defines,
)

if TYPE_CHECKING:
    # Re-export fields, not DRF's serializer classes: a star import of those
    # makes type checkers resolve our public classes to the synchronous ones.
    from rest_framework.exceptions import (
        ValidationError as ValidationError,  # noqa: PLC0414 -- typed export
    )
    from rest_framework.fields import *  # noqa: F403
    from rest_framework.relations import *  # noqa: F403
    from rest_framework.serializers import (
        SerializerMetaclass as SerializerMetaclass,  # noqa: PLC0414 -- typed export
    )
else:
    from rest_framework.serializers import *  # noqa: F403 -- preserve DRF's runtime exports


class _AData:
    """
    ``await serializer.adata()`` is the primary spelling, like Django's
    ``await request.auser()``. ``await serializer.adata`` (adrf's async
    property) keeps working for code migrated from adrf.
    """

    __slots__ = ("serializer",)

    def __init__(self, serializer: Any) -> None:
        self.serializer = serializer

    def __call__(self) -> Coroutine[Any, Any, Any]:
        return default_data(self.serializer)

    def __await__(self) -> Generator[Any, None, Any]:
        return default_data(self.serializer).__await__()


class _ADataDescriptor:
    def __get__(self, instance: Any, owner: type | None = None) -> Any:
        if instance is None:
            return self
        return _AData(instance)


if TYPE_CHECKING:
    # The mixin is only ever combined with a DRF serializer.
    _SerializerBase = serializers.BaseSerializer
else:
    _SerializerBase = object


class AsyncSerializerMixin(_SerializerBase):
    # The async API shared by every aiodrf serializer class. (A comment, not
    # a docstring: drf-spectacular would show it for undocumented serializers.)

    _async_serializer_bridge = True

    adata = _ADataDescriptor()

    async def ais_valid(self, *, raise_exception: bool = False) -> bool:
        return await aio.default_is_valid(self, raise_exception=raise_exception)

    async def asave(self, **kwargs: Any) -> Any:
        return await aio.default_save(self, **kwargs)

    async def arun_validation(self, data: Any = empty) -> Any:
        return await aio.default_run_validation(self, data)

    async def ato_representation(self, instance: Any) -> Any:
        return await aio.default_to_representation(self, instance)

    # ``acreate`` and ``aupdate`` are deliberately not defined: when they are
    # missing, ``asave`` runs ``create`` / ``update`` in one thread hop, inside
    # ``transaction.atomic`` with ``ATOMIC_SAVE``. Define them with
    # ``async def`` to take over.

    # -- Sync bridges ---------------------------------------------------------
    #
    # Sync callers (sync views, the browsable API, schema generation, a
    # ``perform_create`` written for DRF) reach the async members through
    # ``async_to_sync``. That works from a thread and fails loudly on the
    # event loop, where the async API must be used. The bridges go through
    # the dispatchers of :mod:`aiodrf.aio`, so a user's override of an async
    # member takes effect here as well.

    @bridges_to("ais_valid")
    def is_valid(self, *, raise_exception: bool = False) -> bool:
        return super().is_valid(raise_exception=raise_exception)

    @bridges_to("asave")
    def save(self, **kwargs: Any) -> Any:
        # ``ATOMIC_SAVE`` holds for a ``perform_create`` written for DRF too.
        return _atomic(self, super().save)(**kwargs)

    def run_validation(self, data: Any = empty) -> Any:
        if validation_kind(self) is Kind.ASYNC:
            return _bridge(self, "run_validation", data)
        return super().run_validation(data)

    def to_representation(self, instance: Any) -> Any:
        if has_async_representation(self):
            return _bridge(self, "to_representation", instance)
        ret = super().to_representation(instance)
        _check_no_coroutines(self, ret)
        return ret

    # Without these, a synchronous ``save()`` (a ``perform_create`` written
    # for DRF, the browsable API) would skip an ``acreate`` / ``aupdate`` that
    # has no synchronous twin and fall back to DRF's default write.

    @bridges_to("acreate")
    def create(self, validated_data: Any) -> Any:
        return super().create(validated_data)

    @bridges_to("aupdate")
    def update(self, instance: Any, validated_data: Any) -> Any:
        return super().update(instance, validated_data)


def _bridge(serializer: Any, name: str, argument: Any) -> Any:
    """Run the async side of ``name`` for its synchronous caller."""
    target = _async_member(serializer, name)
    return bridge_to_async(
        serializer,
        name,
        f"a{name}",
        target,
        serializer,
        argument,
        advice=(
            " From async code, validate, save and represent it with "
            "`await aiodrf.aio.is_valid(serializer)`, `aio.save()` and `aio.data()`."
        ),
    )


def _async_member(serializer: Any, name: str) -> Any:
    """
    What a sync bridge hands over to: the dispatcher when the async member is
    the user's implementation of the pair, so that an ``a<name>`` override
    takes effect for sync callers; the default otherwise. A synchronous
    override that calls ``super()`` arrives here too, and the dispatcher
    would send it straight back to that override.
    """
    if resolve_pair(serializer, name, f"a{name}") is Impl.ASYNC:
        return getattr(aio, name)
    return getattr(aio, f"default_{name}")


# What DRF's fields represent values as, none of which can hold a coroutine.
_PLAIN_VALUES = frozenset({str, int, float, bool, type(None)})

# DRF's fields whose representation converts the value (``str()``, ``int()``,
# ...): a coroutine becomes an error or a converted value there, never output.
_CONVERTING_FIELDS = frozenset(
    {
        serializers.CharField,
        serializers.EmailField,
        serializers.RegexField,
        serializers.SlugField,
        serializers.URLField,
        serializers.IPAddressField,
        serializers.IntegerField,
        serializers.FloatField,
        serializers.DecimalField,
        serializers.BooleanField,
        *([BigIntegerField] if BigIntegerField is not None else []),
    }
)


@class_cache
def _represents_with_drf(serializer_class: type) -> bool:
    """
    Whether the representation aiodrf's check reads is DRF's own
    ``Serializer.to_representation``: one value per readable field, produced
    by that field.
    """
    mro = serializer_class.__mro__
    for klass in mro[mro.index(AsyncSerializerMixin) + 1 :]:
        if "to_representation" in vars(klass):
            return (
                vars(klass)["to_representation"]
                is serializers.Serializer.to_representation
            )
    return False


def _checked_fields(serializer: Any) -> tuple[tuple[str, bool], ...] | None:
    """
    The ``(name, nested)`` pairs of the fields whose values may hold a
    coroutine, in order, or None when any value may. Fields that convert what
    they represent (``str()``, ``int()``, ...) cannot; an aiodrf serializer
    nested checks its own output, so only its value itself is looked at. Kept
    on the instance, as its classification is (a list's child represents
    every item), unless the project's ``to_representation`` may change the
    fields between items.
    """
    try:
        return serializer.__dict__["_aiodrf_checked_fields"]
    except KeyError:
        pass
    checked = None
    if _represents_with_drf(type(serializer)) and not user_defines(
        serializer, "to_representation"
    ):
        checked = tuple(
            (field.field_name, _checks_itself(field))
            for field in serializer._readable_fields
            if type(field) not in _CONVERTING_FIELDS
            or "to_representation" in vars(field)
        )
        serializer.__dict__["_aiodrf_checked_fields"] = checked
    return checked


def _checks_itself(field: Any) -> bool:
    """Whether ``field`` is an aiodrf serializer, which checks its output."""
    child = field.child if isinstance(field, serializers.ListSerializer) else field
    return isinstance(field, AsyncSerializerMixin) and isinstance(
        child, AsyncSerializerMixin
    )


def _check_no_coroutines(serializer: Any, ret: Any) -> None:
    if not isinstance(ret, dict):
        return
    checked = _checked_fields(serializer)
    if checked is None:
        checked = tuple(
            (name, _checks_itself(serializer.fields.get(name))) for name in ret
        )
    for name, nested in checked:
        value = ret.get(name)
        if type(value) in _PLAIN_VALUES:
            continue
        # ``CoroutineType`` has no subclasses: ``inspect.iscoroutine``.
        if type(value) is types.CoroutineType:
            value.close()
            raise _produced_a_coroutine(serializer, name)
        # A nested serializer checked its own output; a method field's value
        # may hold one anywhere.
        if (
            not nested
            and isinstance(value, (list, tuple, dict))
            and _close_coroutines(value)
        ):
            raise _produced_a_coroutine(serializer, name)


def _produced_a_coroutine(serializer: Any, name: str) -> TypeError:
    return TypeError(
        f"{type(serializer).__qualname__}.{name} produced a coroutine. "
        "Async sources aiodrf cannot detect statically (for example "
        "async properties of non-model objects) need an "
        "`async def get_<field>()` on a SerializerMethodField."
    )


def _close_coroutines(value: Any) -> bool:
    """Close the coroutines in the lists, tuples and dicts of ``value``; True if any."""
    found = False
    pending = [value]
    seen = {id(value)}
    while pending:
        current = pending.pop()
        items = current.values() if isinstance(current, dict) else current
        for item in items:
            if type(item) in _PLAIN_VALUES:
                continue
            if type(item) is types.CoroutineType:
                item.close()
                found = True
            elif isinstance(item, (list, tuple, dict)) and id(item) not in seen:
                seen.add(id(item))
                pending.append(item)
    return found


class BaseSerializer(AsyncSerializerMixin, serializers.BaseSerializer):
    #: Used by ``many=True`` unless ``Meta.list_serializer_class`` is set.
    default_list_serializer_class: type[serializers.ListSerializer] | None = None

    @classmethod
    def many_init(cls, *args: Any, **kwargs: Any) -> Any:
        """
        DRF's ``many_init``, defaulting to aiodrf's ``ListSerializer``.
        """
        list_kwargs = {}
        for key in LIST_SERIALIZER_KWARGS_REMOVE:
            value = kwargs.pop(key, None)
            if value is not None:
                list_kwargs[key] = value
        list_kwargs["child"] = cls(*args, **kwargs)
        list_kwargs.update(
            {
                key: value
                for key, value in kwargs.items()
                if key in LIST_SERIALIZER_KWARGS
            }
        )
        meta = getattr(cls, "Meta", None)
        list_serializer_class = getattr(
            meta,
            "list_serializer_class",
            cls.default_list_serializer_class or ListSerializer,
        )
        return list_serializer_class(*args, **list_kwargs)


class Serializer(BaseSerializer, serializers.Serializer):
    def get_fields(self) -> Any:
        # django-fastdrf's field cache; model serializers use their separate,
        # guarded model-field template.
        return _serializer_fields(self, super().get_fields)


class ListSerializer(AsyncSerializerMixin, serializers.ListSerializer):
    _async_serializer_bridge = True

    def to_representation(self, data: Any) -> Any:
        if has_async_representation(self):
            return _bridge(self, "to_representation", data)
        child: Any = self.child
        if (
            type(child).to_representation is AsyncSerializerMixin.to_representation
            and "to_representation" not in vars(child)
            and _represents_with_drf(type(child))
            and not has_async_representation(child)
        ):
            # DRF's loop, with what the child's ``to_representation`` would do
            # for each item (DRF's, then the check) decided once for the list.
            iterable = data.all() if isinstance(data, BaseManager) else data
            represent = serializers.Serializer.to_representation
            ret = []
            for item in iterable:
                represented = represent(child, item)
                _check_no_coroutines(child, represented)
                ret.append(represented)
            return ret
        return serializers.ListSerializer.to_representation(self, data)


class ModelSerializer[ModelT: Model](Serializer, serializers.ModelSerializer):
    instance: ModelT | None

    def get_fields(self) -> Any:
        """
        DRF's fields. With ``FASTDRF["CACHE_SERIALIZER_FIELDS"]``, a class
        whose fields depend on nothing but the class builds them once, and
        each instance gets a deep copy: the isolation DRF gives declared
        fields (``Field.__deepcopy__`` instantiates the field again from its
        arguments; validators are shared).
        """
        return _model_serializer_fields(self, super().get_fields)

    if TYPE_CHECKING:
        # The model type for type checkers only: at runtime these would be
        # members of a class that is not a bridge, found before the bridge
        # (``aio._common._sync_member``), and ``save()`` would enter
        # ``ATOMIC_SAVE``'s transaction twice.
        async def asave(self, **kwargs: Any) -> ModelT: ...

        def save(self, **kwargs: Any) -> ModelT: ...


class HyperlinkedModelSerializer[ModelT: Model](
    ModelSerializer[ModelT], serializers.HyperlinkedModelSerializer
):
    pass


for _cls in (
    AsyncSerializerMixin,
    BaseSerializer,
    Serializer,
    ListSerializer,
    ModelSerializer,
    HyperlinkedModelSerializer,
):
    bridge_base(_cls)
