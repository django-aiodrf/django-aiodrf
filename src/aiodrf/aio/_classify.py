"""Classification: which validation and representation code may run on the loop."""

import enum
import inspect
import weakref
from typing import Any

from django.core import validators as django_validators
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
from django.core.signals import setting_changed
from django.db import models
from django.utils.choices import CallableChoiceIterator
from rest_framework import fields, relations, serializers
from rest_framework import validators as drf_validators

from aiodrf.settings import aiodrf_settings
from aiodrf.utils import (
    Impl,
    class_cache,
    is_async_callable,
    is_framework_class,
    is_pure,
    is_pure_function,
    resolve_pair,
    user_defines,
)

_BUILTIN_VALIDATORS = frozenset(
    value
    for value in vars(django_validators).values()
    if inspect.isclass(value) and value.__module__ == django_validators.__name__
) | {drf_validators.ProhibitSurrogateCharactersValidator}

_BUILTIN_FIELDS = frozenset(
    value
    for module in (fields, relations)
    for value in vars(module).values()
    if inspect.isclass(value) and value.__module__ == module.__name__
)


class Kind(enum.IntEnum):
    """How a piece of validation code may run, ordered by cost."""

    PURE = 0
    UNKNOWN = 1
    IMPURE = 2
    ASYNC = 3


def _validator_is_async(validator: Any) -> bool:
    if is_async_callable(validator):
        return True
    if inspect.isroutine(validator) or not callable(validator):
        return False
    return is_async_callable(type(validator).__call__)


def _validator_kind(validator: Any) -> Kind:
    # Django's and DRF's own validators first: they make up nearly all
    # validators, and finding out whether a callable is async is the
    # expensive part of planning.
    if _validator_is_async(validator):
        return Kind.ASYNC
    if type(validator) in _BUILTIN_VALIDATORS:
        # ``MinValueValidator(lambda: ...)``: the limit is the project's code.
        limit = getattr(validator, "limit_value", None)
        if callable(limit) and not is_pure_function(limit):
            return Kind.UNKNOWN
        return Kind.PURE
    if isinstance(
        validator,
        (
            drf_validators.UniqueValidator,
            drf_validators.UniqueTogetherValidator,
            drf_validators.BaseUniqueForValidator,
        ),
    ):
        return Kind.IMPURE
    if inspect.isroutine(validator):
        return Kind.PURE if is_pure_function(validator) else Kind.UNKNOWN
    return Kind.PURE if is_pure(validator, "__call__") else Kind.UNKNOWN


def _validators_kind(validators: Any) -> Kind:
    return max((_validator_kind(v) for v in validators), default=Kind.PURE)


def _hook(serializer: Any, sync_name: str, async_name: str) -> tuple[Any, Kind | None]:
    """
    Return ``(hook, kind)`` for an optional hook such as ``validate_<field>``,
    or ``(None, None)``.

    DRF finds these with ``getattr`` on the *instance*, so a hook provided by
    ``__getattr__`` counts too. ``a<name>`` is the async member of the pair.
    """
    sync_hook = getattr(serializer, sync_name, None)
    async_hook = getattr(serializer, async_name, None)
    if sync_hook is None and async_hook is None:
        return None, None
    cls = type(serializer)
    impl = resolve_pair(serializer, sync_name, async_name)
    if sync_hook is None or (async_hook is not None and impl is Impl.ASYNC):
        return async_hook, Kind.ASYNC
    if sync_name in vars(serializer) or not hasattr(cls, sync_name):
        # Set on the instance or provided by ``__getattr__``: only the hook
        # itself can tell. For a method of the class ``resolve_pair`` has
        # the answer cached, and looking again costs more than the rest of
        # the classification.
        if is_async_callable(sync_hook):
            return sync_hook, Kind.ASYNC
    elif impl is Impl.SYNC_IS_ASYNC:
        return sync_hook, Kind.ASYNC
    elif impl is Impl.BASE:
        # DRF's own default, such as ``Serializer.validate``.
        return sync_hook, Kind.PURE
    if is_pure(serializer, sync_name) or is_pure_function(
        getattr(sync_hook, "__func__", sync_hook)
    ):
        return sync_hook, Kind.PURE
    return sync_hook, Kind.UNKNOWN


def _hook_kind(serializer: Any, sync_name: str, async_name: str) -> Kind | None:
    return _hook(serializer, sync_name, async_name)[1]


_CONVERSION_HOOKS = (
    "get_value",
    "get_default",
    "validate_empty_values",
    "to_internal_value",
    "run_validation",
)


def _conversion_kind(field: Any, *, children: bool = True) -> Kind:
    """
    Kind of ``field.run_validation()`` *without* its validators: empty value
    handling, defaults and ``to_internal_value``. A field can need a thread
    for this and still have validators that must be awaited.
    """
    for name in (*_CONVERSION_HOOKS, "run_validators"):
        if is_async_callable(getattr(field, name)):
            raise ImproperlyConfigured(
                f"Field.{name}() must be synchronous. Use an async serializer validation hook instead."
            )
    kind = Kind.PURE
    if isinstance(
        field, (relations.RelatedField, relations.ManyRelatedField, fields.ImageField)
    ):
        # Relations look up their queryset; ImageField opens the upload.
        kind = Kind.IMPURE
    # One cached answer for DRF's own fields, which is nearly all of them.
    if user_defines(field, *_CONVERSION_HOOKS) and any(
        user_defines(field, name) and not is_pure(field, name)
        for name in _CONVERSION_HOOKS
    ):
        kind = max(kind, Kind.UNKNOWN)
    kind = max(kind, _default_kind(field))
    # DRF validates the children of collections synchronously, inside
    # ``to_internal_value``.
    if not children:
        return kind
    for child in (
        getattr(field, "child", None),
        getattr(field, "child_relation", None),
    ):
        if child is None:
            continue
        child_kind = _field_value_kind(child)
        if child_kind is Kind.ASYNC and (
            type(field) not in (fields.ListField, fields.DictField)
            or any(
                name in vars(field)
                for name in (*_CONVERSION_HOOKS, "run_child_validation")
            )
        ):
            raise ImproperlyConfigured(
                f"The child of `{field.field_name}` has async validation, which DRF "
                "would call without awaiting. Validate the items in an "
                f"`async def validate_{field.field_name}()` instead."
            )
        kind = max(kind, child_kind)
    return kind


def _default_kind(field: Any) -> Kind:
    default = getattr(field, "default", fields.empty)
    if default is not fields.empty and is_async_callable(default):
        raise ImproperlyConfigured(
            "Field defaults must be synchronous; use an async serializer validation hook."
        )
    if user_defines(field, "get_default") and not is_pure(field, "get_default"):
        return Kind.UNKNOWN
    if (
        default is not fields.empty  # a class, hence callable
        and callable(default)
        and not isinstance(default, fields.CurrentUserDefault)
        and not is_pure_function(default)
    ):
        return Kind.UNKNOWN
    return Kind.PURE


def _field_value_kind(field: Any) -> Kind:
    if isinstance(field, serializers.BaseSerializer):
        return max(validation_kind(field), _default_kind(field))
    kind = max(_conversion_kind(field), _validators_kind(field.validators))
    if user_defines(field, "run_validators") and not is_pure(field, "run_validators"):
        kind = max(kind, Kind.UNKNOWN)
    return kind


def empty_values_kind(serializer: Any) -> Kind:
    """
    Kind of ``serializer.validate_empty_values()``, which a parent calls
    before an async serializer's own validation: its default and overrides.
    """
    kind = _default_kind(serializer)
    for name in ("get_value", "validate_empty_values"):
        if user_defines(serializer, name) and not is_pure(serializer, name):
            kind = max(kind, Kind.UNKNOWN)
    return kind


def read_only_defaults_kind(serializer: Any) -> Kind:
    """Kind of ``Serializer._read_only_defaults()``, part of ``run_validators()``."""
    kind = Kind.PURE
    for field in serializer.fields.values():
        if field.read_only:
            kind = max(kind, _default_kind(field))
    return kind


@class_cache
def _has_async_attribute(model: Any, name: str) -> bool:
    # Asked for every field of every serializer instance. Uncached, the
    # static lookup and the unwrapping were two thirds of the cost of
    # ``has_async_representation``.
    try:
        attr = inspect.getattr_static(model, name)
    except AttributeError:
        return False
    if isinstance(attr, property):
        attr = attr.fget
    elif isinstance(attr, (staticmethod, classmethod)):
        attr = attr.__func__
    return attr is not None and is_async_callable(attr)


@class_cache
def _related_model(model: Any, name: str) -> type[models.Model] | None:
    # The model a forward relation such as ``book`` in ``book.asummary`` leads to.
    try:
        field = model._meta.get_field(name)
    except (AttributeError, FieldDoesNotExist):
        return None
    if field.many_to_one or field.one_to_one:
        return field.related_model
    return None


def _is_async_source(model: Any, source_attrs: Any) -> bool:
    if not source_attrs:
        return False
    for name in source_attrs[:-1]:
        if model is None:
            return False
        model = _related_model(model, name)
    return model is not None and _has_async_attribute(model, source_attrs[-1])


def _field_repr_is_async(serializer: Any, field: Any, model: Any) -> bool:
    if getattr(field, "_aiodrf_async_field", False):
        # A field of aiodrf.contrib.adrf_compat with adrf's ``ato_representation``.
        return True
    if isinstance(field, serializers.BaseSerializer):
        return has_async_representation(field)
    for name in ("get_attribute", "to_representation"):
        if is_async_callable(getattr(field, name)):
            raise ImproperlyConfigured(
                f"Field.{name}() must be synchronous. Use an async SerializerMethodField method instead."
            )
    if isinstance(field, fields.SerializerMethodField):
        method = getattr(serializer, field.method_name, None)
        return method is not None and is_async_callable(method)
    return _is_async_source(model, getattr(field, "source_attrs", ()))


class Plan:
    """
    Static analysis of a serializer instance.

    ``validation`` is the most expensive :class:`Kind` found anywhere in the
    validation code, nested serializers included; ``async_representation``
    tells whether producing ``.data`` requires awaiting anything.
    """

    __slots__ = ("async_representation", "validation")

    def __init__(self, validation: Any, async_representation: Any) -> None:
        self.validation = validation
        self.async_representation = async_representation

    def __repr__(self) -> str:
        return (
            f"<Plan validation={self.validation.name} "
            f"async_representation={self.async_representation}>"
        )


def plan_for(serializer: Any) -> Plan:
    return Plan(validation_kind(serializer), has_async_representation(serializer))


# The halves are computed separately, once per serializer instance: a list or
# retrieve request never validates, so it does not pay for classifying
# validators. Each result is stored under its own key of the instance
# ``__dict__`` (an object holding both, and the serializer, would be a
# reference cycle). When the fields of a serializer are a function of its
# class, the result is a function of the class as well and is kept per class:
# walking the fields costs a few microseconds with the GIL and, on a
# free-threaded interpreter under load, a few hundred (every registry, cache
# and class it touches is shared between threads).

# serializer class -> {"validation": Kind, "representation": bool}
_CLASS_KINDS: weakref.WeakKeyDictionary[type, dict[str, object]] = (
    weakref.WeakKeyDictionary()
)


# The arguments every serializer takes; anything else may change its fields.
_PLAIN_KWARGS = frozenset({"instance", "data", "context", "partial"})


# DRF's hooks that decide which fields a serializer has, and how they are built.
_FIELD_HOOKS = (
    "__init__",
    "__getattr__",
    "fields",
    "get_fields",
    "get_field_names",
    "get_default_field_names",
    "get_extra_kwargs",
    "include_extra_kwargs",
    "get_uniqueness_extra_kwargs",
    "build_field",
    "build_standard_field",
    "build_relational_field",
    "build_nested_field",
    "build_property_field",
    "build_url_field",
    "build_unknown_field",
    "get_validators",
    "get_unique_together_validators",
    "get_unique_for_date_validators",
)


# What DRF materializes on an instance the first time it is read. Once any of
# these exists, the instance's fields or validators may have been changed
# (``serializer.fields["x"].validators.append(...)`` is ordinary DRF); the
# class says nothing about such an instance.
_MATERIALIZED = frozenset(
    {"fields", "_validators", "_readable_fields", "_writable_fields"}
)


def _class_kinds(serializer: Any) -> dict[Any, Any] | None:
    """
    The per-class entry for ``serializer``, or None when its classification
    is not a property of its class: a list serializer (its kind is its
    child's), one whose fields are not a function of its class
    (:func:`is_static`, nested serializers included), one whose validators
    exist already and may have been edited, or one something callable was
    set on (DRF finds a ``validate_<field>`` on the instance too).
    """
    state = vars(serializer)
    if (
        not isinstance(serializer, serializers.Serializer)
        or not is_static(serializer)
        or not _MATERIALIZED.isdisjoint(state)
    ):
        return None
    for value in state.values():
        # ``default`` is ``fields.empty``, a class, on every instance.
        # ``inspect.isclass`` is this ``isinstance``, without a Python call.
        if callable(value) and not isinstance(value, type):
            return None
    cls = type(serializer)
    try:
        return _CLASS_KINDS[cls]
    except KeyError:
        # Racing threads store the same value. An entry created before a
        # ``clear_class_kinds()`` is dropped with the clear, so a result
        # computed with the old settings is never published under the new.
        return _CLASS_KINDS.setdefault(cls, {})


# What DRF sets on a serializer instance over a class attribute of the same
# name. Any other instance attribute that shadows one of the class may change
# what DRF does: a method assigned to the instance, fields materialized (and
# perhaps edited), a ``Meta`` or ``url_field_name`` of its own.
_DRF_SHADOWS = frozenset({"_creation_counter", "initial", "default_empty_html"})

# Declared serializers are deep-copied into every instance, which builds them
# again from their arguments; these hooks could make the copy differ.
_BUILD_HOOKS = (*_FIELD_HOOKS, "bind", "__deepcopy__", "__new__")

# class -> the names an instance must not shadow, or None when it is not static
_static_classes: weakref.WeakKeyDictionary[type, frozenset[str] | None] = (
    weakref.WeakKeyDictionary()
)


def is_static(serializer: Any) -> bool:
    """
    Return True if the fields of ``serializer`` (the child of a list
    serializer), and those of the serializers nested in it, are a function
    of its class.

    That holds for an instance built with the usual arguments only that
    shadows nothing of its class (no fields materialized, no method assigned),
    of a class that builds its fields with DRF's code alone from declarations
    and a model, whose declared fields are DRF's, children included, and
    whose declared serializers are static too. The classification cache and
    the compiler (``aiodrf.contrib.compiler``) both keep per-class answers
    for these alone.
    """
    if isinstance(serializer, serializers.ListSerializer):
        serializer = serializer.child
    names = _instance_shadow_names(type(serializer))
    return (
        names is not None
        and _PLAIN_KWARGS.issuperset(serializer._kwargs)
        and names.isdisjoint(vars(serializer))
    )


#: Set by the generic views on a serializer they built, validated and saved
#: with framework code alone (:func:`fields_from_class`).
FIELDS_FROM_CLASS = "_aiodrf_fields_from_class"


def fields_from_class(serializer: Any) -> bool:
    """
    :func:`is_static` for a serializer whose fields exist on the instance
    because framework code built and used them: a generic view's serializer
    after validation and the save, when no ``get_serializer*`` or
    ``perform_*`` of the project's and no method of the serializer's class
    could have edited them. The generic views mark such an instance
    (:data:`FIELDS_FROM_CLASS`); nothing else is.
    """
    if not getattr(serializer, FIELDS_FROM_CLASS, False):
        return False
    if isinstance(serializer, serializers.ListSerializer):
        serializer = serializer.child
    return _instance_shadow_names(type(serializer)) is not None and (
        _PLAIN_KWARGS.issuperset(serializer._kwargs)
    )


def _instance_shadow_names(cls: type) -> frozenset[str] | None:
    """
    The names an instance of ``cls`` must not set for its fields to be those
    of its class (:func:`is_static`), or None when they never are.
    """
    try:
        return _static_classes[cls]
    except KeyError:
        pass
    meta = getattr(cls, "Meta", None)
    model = getattr(meta, "model", None)
    static = (
        issubclass(cls, serializers.Serializer)
        # A child may build fields from its parent's context, or change them
        # when bound.
        and not getattr(meta, "depth", 0)
        and not user_defines(cls, *_BUILD_HOOKS)
        # The project's code, run whenever a ModelSerializer builds its fields.
        and not (
            issubclass(cls, serializers.ModelSerializer)
            and model is not None
            and _model_fields_call_code(model)
        )
        and all(_static_declaration(field) for field in cls._declared_fields.values())
    )
    names = frozenset(dir(cls)) - _DRF_SHADOWS if static else None
    # Racing threads store the same value.
    return _static_classes.setdefault(cls, names)


def _static_declaration(field: Any) -> bool:
    if isinstance(field, serializers.ListSerializer):
        return not user_defines(field, *_BUILD_HOOKS) and _static_declaration(
            field.child
        )
    if isinstance(field, serializers.BaseSerializer):
        return _instance_shadow_names(type(field)) is not None
    # DRF's own fields; a custom one may bind differently per parent. DRF's
    # collections and to-many relations bind the child they were declared with.
    return type(field) in _BUILTIN_FIELDS and all(
        _static_declaration(child)
        for child in (
            getattr(field, "child", None),
            getattr(field, "child_relation", None),
        )
        if child is not None
    )


def clear_class_kinds(*, setting: Any, **kwargs: Any) -> None:
    # ``is_pure`` reads ``AIODRF["PURE_POLICIES"]``.
    if setting == "AIODRF":
        _CLASS_KINDS.clear()


setting_changed.connect(clear_class_kinds)


def validation_kind(serializer: Any) -> Kind:
    try:
        return serializer.__dict__["_aiodrf_validation"]
    except KeyError:
        pass
    per_class = _class_kinds(serializer)
    if per_class is not None and "validation" in per_class:
        kind = per_class["validation"]
    else:
        kind = _validation_kind(serializer)
        if per_class is not None:
            per_class["validation"] = kind
    serializer.__dict__["_aiodrf_validation"] = kind
    return kind


def _validation_kind(serializer: Any) -> Kind:
    if isinstance(serializer, serializers.ListSerializer):
        kind = max(
            validation_kind(serializer.child),
            _validators_kind(serializer.validators),
            _hook_kind(serializer, "validate", "avalidate") or Kind.PURE,
        )
        overridable = ("run_validation", "to_internal_value", "run_validators")
        kind = _check_overrides(
            serializer, kind, (*overridable, "run_child_validation")
        )
    elif not isinstance(serializer, serializers.Serializer):
        # DRF's BaseSerializer pattern: no fields, its own to_internal_value().
        kind = _validators_kind(serializer.validators)
        kind = _check_overrides(
            serializer, kind, ("run_validation", "to_internal_value", "run_validators")
        )
    else:
        kind = max(
            _validators_kind(serializer.validators),
            _hook_kind(serializer, "validate", "avalidate") or Kind.PURE,
        )
        for field in serializer.fields.values():
            if field.read_only:
                # DRF puts its default in ``validated_data``.
                kind = max(kind, _default_kind(field))
                continue
            kind = max(kind, _field_value_kind(field))
            name = field.field_name
            hook_kind = _hook_kind(serializer, f"validate_{name}", f"avalidate_{name}")
            if hook_kind is not None:
                kind = max(kind, hook_kind)
        kind = _check_overrides(
            serializer, kind, ("run_validation", "to_internal_value", "run_validators")
        )
    return kind


def has_async_representation(serializer: Any) -> bool:
    try:
        return serializer.__dict__["_aiodrf_async_representation"]
    except KeyError:
        pass
    per_class = _class_kinds(serializer)
    if per_class is not None and "representation" in per_class:
        result = per_class["representation"]
    else:
        result = _async_representation(serializer)
        if per_class is not None:
            per_class["representation"] = result
    serializer.__dict__["_aiodrf_async_representation"] = result
    return result


def _async_representation(serializer: Any) -> bool:
    result = _has_async_repr_override(serializer)
    if not result and isinstance(serializer, serializers.ListSerializer):
        result = has_async_representation(serializer.child)
    elif not result and isinstance(serializer, serializers.Serializer):
        model = getattr(getattr(serializer, "Meta", None), "model", None)
        for field in serializer.fields.values():
            if not field.write_only:
                # Do not short-circuit: later nested fields may have querying
                # get_fields() hooks too. Classification runs in the worker.
                result = _field_repr_is_async(serializer, field, model) or result
    return result


def _is_declarative(serializer: Any) -> bool:
    """
    Return True if building and classifying ``serializer`` runs framework
    code only, so it is safe on the event loop.

    That holds when no class before DRF's in the MRO defines a function:
    fields and ``Meta`` are declarations. A ``get_fields()``, an ``__init__``
    or a property written for DRF may query, so such serializers are built
    in the worker, together with the work they are built for.
    """
    if isinstance(serializer, fields.FilePathField) or not is_declarative_class(
        type(serializer)
    ):
        # A FilePathField lists its directory whenever it is built.
        return False
    for name in ("child", "child_relation"):
        child = getattr(serializer, name, None)
        if child is not None and not _is_declarative(child):
            return False
    return True


@class_cache
def is_declarative_class(serializer_class: Any) -> bool:
    """
    Return True if instantiating ``serializer_class`` and building its
    declared fields runs framework code only (see ``_is_declarative``).
    """
    model = getattr(getattr(serializer_class, "Meta", None), "model", None)
    if model is not None and _model_fields_call_code(model):
        return False
    for klass in serializer_class.__mro__:
        if klass.__module__.split(".", 1)[0] in ("rest_framework", "aiodrf"):
            break
        for value in klass.__dict__.values():
            if inspect.isroutine(value) or isinstance(
                value, (property, classmethod, staticmethod)
            ):
                return False
    # Looking at declarations must not materialize context-dependent fields
    # on the loop. Custom children and binding hooks belong in the worker.
    return all(
        _is_declarative(field)
        for field in getattr(serializer_class, "_declared_fields", {}).values()
    )


@class_cache
def _model_fields_call_code(model: Any) -> bool:
    """
    Return True if building a serializer field from a field of ``model`` may
    call the project's code: callable ``choices`` or ``limit_choices_to``
    (commonly a query), a ``limit_choices_to`` applied through the related
    model's default manager of the project's (its ``get_queryset()``), a
    model field class of the project's, whose attributes DRF reads, or a
    FilePathField, which lists its directory.
    """
    for field in model._meta.get_fields():
        if isinstance(field, models.FilePathField) or not is_framework_class(
            type(field)
        ):
            return True
        if isinstance(getattr(field, "choices", None), CallableChoiceIterator):
            return True
        limit_choices_to = getattr(
            getattr(field, "remote_field", None), "limit_choices_to", None
        )
        if callable(limit_choices_to) or (
            limit_choices_to
            and user_defines(field.related_model._default_manager, "get_queryset")
        ):
            return True
    return False


def _has_async_repr_override(serializer: Any) -> bool:
    impl = resolve_pair(serializer, "to_representation", "ato_representation")
    return impl in (Impl.ASYNC, Impl.SYNC_IS_ASYNC)


def _check_overrides(serializer: Any, kind: Any, names: tuple[str, ...]) -> Kind:
    """Account for user overrides of DRF's validation methods."""
    cls = type(serializer)
    for name in names:
        impl = resolve_pair(serializer, name, "a" + name)
        if impl in (Impl.ASYNC, Impl.SYNC_IS_ASYNC):
            kind = Kind.ASYNC
        elif impl is Impl.SYNC and kind is Kind.ASYNC:
            raise ImproperlyConfigured(
                f"{cls.__qualname__} overrides {name}() but has async validation "
                f"hooks, which a synchronous {name}() cannot await. Override "
                f"`async def a{name}()` instead."
            )
        elif impl is Impl.SYNC and not is_pure(cls, name):
            kind = max(kind, Kind.UNKNOWN)
    return kind


def _resolve_unknown(kind: Kind) -> Kind:
    """Apply VALIDATION_UNKNOWN in ``aio._validate`` at each execution stage.

    Keep the cached classification independent of the current policy; the
    validation walker resolves UNKNOWN when it decides where to run a stage.
    """
    if kind is not Kind.UNKNOWN:
        return kind
    return Kind.PURE if aiodrf_settings.VALIDATION_UNKNOWN == "inline" else Kind.IMPURE
