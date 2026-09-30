"""
Whether DRF's representation of model instances can query.

A serializer whose fields are a function of its class (``is_static``) and that
represents with DRF's own code reads one attribute of the instance per field:
a column, which the instance has loaded or reads with a query (a deferred
field); a forward relation, which ``select_related`` cached or a query
fetches; a many relation, which ``prefetch_related`` cached or a query
fetches. The class's *read plan* lists them once; :func:`reads_loaded` checks
an instance, or the items of a list, against it without building fields.
"""

import inspect
from collections.abc import Sequence
from typing import Any, NamedTuple, TypeGuard

from django.core.exceptions import FieldDoesNotExist
from django.db import models
from django.db.models.fields.related_descriptors import (
    ForeignKeyDeferredAttribute,
    ForwardManyToOneDescriptor,
    ForwardOneToOneDescriptor,
    ManyToManyDescriptor,
    ReverseManyToOneDescriptor,
)
from django.db.models.query_utils import DeferredAttribute
from rest_framework import fields, relations, serializers

from aiodrf.aio._classify import _PLAIN_KWARGS, _instance_shadow_names, is_static
from aiodrf.compat import BigIntegerField
from aiodrf.utils import class_cache, user_defines

__all__ = ["reads_loaded"]

# DRF's fields whose representation of a loaded value runs no I/O.
_VALUE_FIELDS = frozenset(
    {
        fields.BooleanField,
        fields.CharField,
        fields.ChoiceField,
        fields.DateField,
        fields.DateTimeField,
        fields.DecimalField,
        fields.DurationField,
        fields.EmailField,
        fields.FloatField,
        fields.IntegerField,
        fields.IPAddressField,
        fields.JSONField,
        fields.ReadOnlyField,
        fields.RegexField,
        fields.SlugField,
        fields.TimeField,
        fields.URLField,
        fields.UUIDField,
        *([BigIntegerField] if BigIntegerField is not None else []),
    }
)


# Checking an object costs about a fiftieth of the thread hop it saves (about
# 4,000 instructions against 200,000); past this many objects the check is no
# longer the cheaper way.
MAX_CHECKED_OBJECTS = 32


class _Plan(NamedTuple):
    model: type[models.Model]
    # Attribute names of the columns read.
    columns: frozenset[str]
    # (relation cache name, key column, the related serializer's plan)
    forward: tuple[tuple[str, str, "_Plan"], ...]
    # (prefetch cache key, the related serializer's plan)
    many: tuple[tuple[str, "_Plan"], ...]
    # Objects read per instance at least: itself and its forward relations.
    objects: int


def reads_loaded(
    serializer: serializers.BaseSerializer,
    source: Any,
    *,
    fields_from_class: bool = False,
) -> bool:
    """
    True if representing ``source`` (an instance, or a list or tuple of them
    for a list serializer) with ``serializer`` reads only what the instances
    have loaded, so it cannot query. Representations of more than
    :data:`MAX_CHECKED_OBJECTS` objects are not checked.

    ``fields_from_class``: the caller knows the serializer's fields, built
    already (it validated), are its class's: nothing of the project's ran on
    it. Its class must still be static.
    """
    if fields_from_class:
        if (
            _instance_shadow_names(type(serializer)) is None
            or "to_representation" in vars(serializer)
            or not _PLAIN_KWARGS.issuperset(vars(serializer).get("_kwargs", ()))
        ):
            return False
    elif not is_static(serializer):
        return False
    if isinstance(serializer, serializers.ListSerializer):
        if (
            type(source) not in (list, tuple)
            or "to_representation" in vars(serializer)
            or not _drf_list(type(serializer))
        ):
            return False
        plan, instances = _plan(type(serializer.child)), source
    else:
        plan, instances = _plan(type(serializer)), (source,)
    if plan is None or len(instances) * plan.objects > MAX_CHECKED_OBJECTS:
        return False
    return _all_loaded(plan, instances, [MAX_CHECKED_OBJECTS])


def _all_loaded(plan: "_Plan", instances: Sequence[Any], budget: list[int]) -> bool:
    """
    Whether each of ``instances`` has loaded what ``plan`` reads, a level at a
    time: the related objects of all instances form the next level. Each level
    is paid from ``budget`` before it is checked.
    """
    budget[0] -= len(instances)
    if budget[0] < 0:
        return False
    model, columns = plan.model, plan.columns
    forward: list[list[Any]] = [[] for _ in plan.forward]
    many: list[list[Any]] = [[] for _ in plan.many]
    for instance in instances:
        if type(instance) is not model:
            return False
        state = instance.__dict__
        if not state.keys() >= columns:
            return False
        if forward:
            # Django's relation descriptors cache in ``_state.fields_cache``.
            cache = state["_state"].fields_cache
            for related, (name, attname, _) in zip(forward, plan.forward, strict=True):
                if name in cache:
                    if cache[name] is not None:
                        related.append(cache[name])
                # Not cached: no query only when the relation is null.
                elif state.get(attname, ...) is not None:
                    return False
        if many:
            prefetched = state.get("_prefetched_objects_cache", {})
            for related, (key, _) in zip(many, plan.many, strict=True):
                if key not in prefetched:
                    return False
                # ``iter()``: a QuerySet's ``__len__`` would be called too.
                related.extend(iter(prefetched[key]))
    levels = zip(forward + many, plan.forward + plan.many, strict=True)
    return all(
        _all_loaded(related_plan, related, budget)
        for related, (*_, related_plan) in levels
    )


@class_cache
def _plan(serializer_class: type) -> "_Plan | None":
    """The read plan of a static serializer class, or None."""
    model = getattr(getattr(serializer_class, "Meta", None), "model", None)
    if (
        not (isinstance(model, type) and issubclass(model, models.Model))
        or _instance_shadow_names(serializer_class) is None
        or not _drf_member(serializer_class, "to_representation")
    ):
        return None
    columns, forward, many = set(), [], []
    # Static: the fields are a function of the class, built by DRF's code.
    for field in serializer_class().fields.values():
        if field.write_only:
            continue
        if field.source == "*" or len(field.source_attrs) != 1:
            return None
        try:
            model_field = model._meta.get_field(field.source_attrs[0])
        except FieldDoesNotExist:
            return None
        if not _drf_member(type(field), "get_attribute") or not _djangos_descriptor(
            model, field.source_attrs[0]
        ):
            return None
        if isinstance(field, serializers.ListSerializer):
            related = _many_relation(field, model_field)
            if related is None:
                return None
            many.append(related)
        elif isinstance(field, serializers.Serializer):
            related_plan = _plan(type(field))
            if (
                related_plan is None
                or related_plan.model is not model_field.related_model
                or not _forward(model_field)
            ):
                return None
            forward.append((model_field.cache_name, model_field.attname, related_plan))
        elif type(field) is relations.PrimaryKeyRelatedField and _forward(model_field):
            # DRF reads the key column (``use_pk_only_optimization``) through
            # ``serializable_value``.
            if (
                field.pk_field is not None and type(field.pk_field) not in _VALUE_FIELDS
            ) or model.serializable_value is not models.Model.serializable_value:
                return None
            if not _djangos_descriptor(model, model_field.attname):
                return None
            columns.add(model_field.attname)
        elif (
            type(field) in _VALUE_FIELDS
            and model_field.concrete
            and not model_field.is_relation
            and model_field.attname == field.source_attrs[0]
        ):
            columns.add(model_field.attname)
        else:
            return None
    objects = 1 + sum(related_plan.objects for *_, related_plan in forward)
    return _Plan(model, frozenset(columns), tuple(forward), tuple(many), objects)


# What Django's model fields install on the class; anything else under a
# field's name (a property of a subclass or proxy) is the project's code.
_DESCRIPTORS = frozenset(
    {
        DeferredAttribute,
        ForeignKeyDeferredAttribute,
        ForwardManyToOneDescriptor,
        ForwardOneToOneDescriptor,
        ManyToManyDescriptor,
        ReverseManyToOneDescriptor,
    }
)


def _djangos_descriptor(model: type, name: str) -> bool:
    """Whether reading ``name`` of a ``model`` instance runs Django's code only."""
    return type(inspect.getattr_static(model, name, None)) in _DESCRIPTORS


def _many_relation(field: Any, model_field: Any) -> tuple[str, "_Plan"] | None:
    """``(prefetch cache key, plan)`` of a nested list of related objects, or None."""
    related_plan = _plan(type(field.child))
    if (
        related_plan is None
        or related_plan.model is not model_field.related_model
        or not _drf_list(type(field))
    ):
        return None
    # The related manager DRF calls ``all()`` on is a subclass of the default
    # manager's class, built by Django for the instance.
    if user_defines(
        model_field.related_model._default_manager.__class__, "all", "__init__"
    ):
        return None
    if model_field.many_to_many and not model_field.auto_created:
        # Django's forward many-to-many manager caches under the field's name.
        return model_field.name, related_plan
    if model_field.one_to_many:
        # A reverse foreign key's manager caches under its relation's name.
        return model_field.cache_name, related_plan
    return None


def _forward(model_field: Any) -> TypeGuard[models.ForeignKey]:
    """A foreign key or forward one-to-one field, read through its descriptor."""
    return model_field.concrete and (model_field.many_to_one or model_field.one_to_one)


@class_cache
def _drf_member(cls: type, name: str) -> bool:
    """Whether ``cls`` runs DRF's own ``name``, behind aiodrf's wrappers at most."""
    from aiodrf.serializers import AsyncSerializerMixin, ListSerializer

    # They hand DRF's representation over unless it is asynchronous, which
    # the fields a plan accepts are not.
    wrappers = (AsyncSerializerMixin, ListSerializer)
    for klass in cls.__mro__:
        if name in vars(klass) and klass not in wrappers:
            return klass.__module__.startswith("rest_framework.")
    return False


def _drf_list(list_class: type) -> bool:
    return _drf_member(list_class, "to_representation")
