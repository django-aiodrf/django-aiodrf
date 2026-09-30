"""Class-scoped templates for opt-in serializer field copying."""

from collections.abc import Callable
from typing import Any

from django.core.exceptions import FieldDoesNotExist
from django.core.signals import setting_changed
from django.db.models.manager import BaseManager
from django.utils.functional import lazy
from rest_framework.utils.field_mapping import get_unique_error_message
from rest_framework.validators import UniqueValidator

from aiodrf.aio._classify import _FIELD_HOOKS, _model_fields_call_code
from aiodrf.utils import class_cache, user_defines

# What would make an instance's fields differ from its class's, if set on the
# instance: ``Meta``, the attributes and hooks DRF's ``get_fields`` reads.
_FIELD_STATE = frozenset(
    {
        "Meta",
        "_declared_fields",
        "url_field_name",
        "serializer_field_mapping",
        "serializer_related_field",
        "serializer_related_to_field",
        "serializer_url_field",
        "serializer_choice_field",
        *_FIELD_HOOKS,
    }
)


def _fields_are_static(serializer: Any) -> bool:
    """
    Whether DRF would build the same fields for every instance of the class.
    The constructor's arguments do not matter: DRF's ``__init__`` only stores
    them, and a class with an ``__init__`` of its own is not static.

    Shared by ``serializers.ModelSerializer.get_fields`` and the automatic
    prefetch inspector in ``contrib.builtin.prefetch``.
    """
    return _class_fields_are_static(type(serializer)) and _FIELD_STATE.isdisjoint(
        vars(serializer)
    )


@class_cache
def _class_fields_are_static(cls: type[Any]) -> bool:
    """Whether ``cls`` builds its fields from its declarations with DRF's code."""
    meta = getattr(cls, "Meta", None)
    model = getattr(meta, "model", None)
    return (
        not user_defines(cls, "__new__", *_FIELD_HOOKS)
        # ``Meta.depth`` builds a serializer class per nested relation.
        and not getattr(meta, "depth", 0)
        and (model is None or not _model_fields_call_code(model))
    )


@class_cache
def _field_template(cls: type[Any]) -> dict[str, Any]:
    # Built on an instance of its own: a static class's fields are the same
    # for every instance, and this one is never bound or handed out.
    from aiodrf.serializers import ModelSerializer

    serializer = cls()
    template = super(ModelSerializer, serializer).get_fields()
    model = getattr(getattr(cls, "Meta", None), "model", None)
    if model is not None:
        extra_kwargs = serializer.get_extra_kwargs()
        # Validators DRF built from the model: not those of a declared field
        # or given in ``extra_kwargs``, which DRF keeps as the project wrote them.
        built = {
            name: field
            for name, field in template.items()
            if name not in cls._declared_fields
            and "validators" not in extra_kwargs.get(name, {})
        }
        _translate_unique_messages(model, built)
    return template


def _translate_unique_messages(model: Any, fields: dict[str, Any]) -> None:
    """
    DRF formats the message of a ``UniqueValidator`` it builds for a model
    field (``get_unique_error_message``) in the language active at the time.
    Every copy of the template shares its validators, as DRF shares declared
    ones: format the message again whenever it is read instead.
    """
    for name, field in fields.items():
        try:
            model_field = model._meta.get_field(field.source or name)
        except FieldDoesNotExist:
            continue
        message = get_unique_error_message(model_field)
        if message is None:
            continue
        translated: Any = lazy(get_unique_error_message, str)(model_field)
        for validator in field._kwargs.get("validators", ()):
            # Only DRF's message: one given in ``extra_kwargs`` stays.
            if type(validator) is UniqueValidator and validator.message == message:
                validator.message = translated


@class_cache
def _template_memo(cls: type[Any]) -> dict[int, object]:
    """
    ``copy.deepcopy`` memo entries that keep the managers DRF passed to the
    fields it built (``queryset=related_model._default_manager``) shared by
    every copy, as DRF's per-instance build shares them. Deep-copying one
    fails on state a manager may hold (a lock, a client). Declared fields are
    left to ``deepcopy``, as DRF copies them.
    """
    declared = cls._declared_fields
    memo: dict[int, object] = {}
    for name, field in _field_template(cls).items():
        if name not in declared:
            _collect_managers(field, memo)
    return memo


def _collect_managers(field: Any, memo: dict[int, object]) -> None:
    for value in field._kwargs.values():
        if isinstance(value, BaseManager):
            memo[id(value)] = value
        elif hasattr(value, "_kwargs"):  # ``child_relation`` of a to-many field
            _collect_managers(value, memo)


@class_cache
def _field_copy_plan(cls: type[Any]) -> Callable[[], dict[str, Any]]:
    from aiodrf.contrib.builtin.field_copy import plan_fields

    return plan_fields(_field_template(cls), shared=_template_memo(cls))


@class_cache
def _compiled_field_copy_plan(cls: type[Any]) -> Callable[[], dict[str, Any]]:
    from aiodrf.contrib.builtin.field_copy import compile_fields

    return compile_fields(_field_template(cls), shared=_template_memo(cls))


@class_cache
def _declared_copy_plan(cls: type[Any]) -> Callable[[], dict[str, Any]]:
    from aiodrf.contrib.builtin.field_copy import compile_fields

    return compile_fields(cls._declared_fields)


def _clear_field_templates(*, setting: str, **kwargs: Any) -> None:
    # ``REST_FRAMEWORK["URL_FIELD_NAME"]`` names a field. Settings that fields
    # read when instantiated are read again by each copy.
    if setting in ("AIODRF", "REST_FRAMEWORK"):
        _field_template.cache_clear()
        _template_memo.cache_clear()
        _field_copy_plan.cache_clear()
        _compiled_field_copy_plan.cache_clear()
        _declared_copy_plan.cache_clear()


setting_changed.connect(_clear_field_templates)
