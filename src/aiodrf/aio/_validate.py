"""Validation: ``is_valid``, ``run_validation`` and the async field walker."""

import warnings
from collections.abc import Callable, Iterator, Mapping
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.core.exceptions import ValidationError as DjangoValidationError
from fastdrf._relations import _batch_related_lookups, _unbatch
from fastdrf.settings import fastdrf_settings
from rest_framework import fields, relations, serializers
from rest_framework.exceptions import ValidationError
from rest_framework.fields import SkipField, get_error_detail
from rest_framework.serializers import as_serializer_error
from rest_framework.settings import api_settings
from rest_framework.utils import html

from aiodrf.aio._classify import (
    Kind,
    _conversion_kind,
    _field_value_kind,
    _hook,
    _is_declarative,
    _resolve_unknown,
    _validator_kind,
    _validators_kind,
    empty_values_kind,
    read_only_defaults_kind,
    validation_kind,
)
from aiodrf.aio._common import NEEDS_AWAIT, _acall, _bridged, _sync_member
from aiodrf.compat import DRF_HAS_LIST_ERRORS_AS_DICT
from aiodrf.settings import aiodrf_settings
from aiodrf.utils import (
    Impl,
    resolve_pair,
    run_sync,
)


async def is_valid(serializer: Any, *, raise_exception: bool = False) -> bool:
    """Async counterpart of ``serializer.is_valid()`` for any DRF serializer."""
    impl = resolve_pair(serializer, "is_valid", "ais_valid")
    if impl is Impl.ASYNC:
        return await _acall(serializer.ais_valid, raise_exception=raise_exception)
    if impl is Impl.SYNC_IS_ASYNC:
        return await _acall(serializer.is_valid, raise_exception=raise_exception)
    if impl is Impl.SYNC:
        return await _acall(_own_is_valid, serializer, raise_exception)
    return await default_is_valid(serializer, raise_exception=raise_exception)


async def default_is_valid(serializer: Any, *, raise_exception: bool = False) -> bool:
    """The implementation behind :func:`is_valid` and ``ais_valid()``."""
    assert hasattr(serializer, "initial_data"), (  # noqa: S101 -- as in DRF
        "Cannot call `.is_valid()` as no `data=` keyword argument was "
        "passed when instantiating the serializer instance."
    )
    if hasattr(serializer, "_validated_data"):
        return _finish_is_valid(serializer, raise_exception)

    result: Any = None
    inline = aiodrf_settings.VALIDATION_UNKNOWN == "inline"
    if _classifies_inline(serializer):
        kind = validation_kind(serializer)
        if kind is Kind.PURE or (kind is Kind.UNKNOWN and inline):
            return _sync_is_valid(serializer, raise_exception)
        if kind is Kind.ASYNC:
            result = NEEDS_AWAIT
    if result is None:
        # One hop: the worker builds the fields, classifies them and, unless
        # something has to be awaited, validates right there.
        result = await run_sync(_try_default_is_valid)(serializer, raise_exception)
    if result is not NEEDS_AWAIT:
        return result

    empty: list[Any] | dict[str, Any] = (
        [] if isinstance(serializer, serializers.ListSerializer) else {}
    )
    batched = _batch_related_lookups(serializer)
    try:
        serializer._validated_data = await run_validation(
            serializer, serializer.initial_data
        )
    except ValidationError as exc:
        serializer._validated_data = empty
        serializer._errors = exc.detail
    else:
        serializer._errors = empty
    finally:
        _unbatch(batched)
    return _finish_is_valid(serializer, raise_exception)


def _classifies_inline(serializer: Any) -> bool:
    # Classification builds the fields, which may run the project's code
    # (``get_fields``, field factories); on the loop only when a worker did
    # it already (generic actions), the fields are declared, or the project
    # asserts that its validation code does no I/O.
    return (
        aiodrf_settings.VALIDATION_UNKNOWN == "inline"
        or "_aiodrf_validation" in serializer.__dict__
        or _is_declarative(serializer)
    )


def try_is_valid(serializer: Any, *, raise_exception: bool = False) -> Any:
    """
    The synchronous half of :func:`is_valid`, for code that already runs in
    a worker thread: DRF's ``is_valid()``, or :data:`NEEDS_AWAIT` when the
    serializer has validation that must be awaited.
    """
    impl = resolve_pair(serializer, "is_valid", "ais_valid")
    if impl in (Impl.ASYNC, Impl.SYNC_IS_ASYNC):
        return NEEDS_AWAIT
    if impl is Impl.SYNC:
        return _own_is_valid(serializer, raise_exception)
    return _try_default_is_valid(serializer, raise_exception)


def _own_is_valid(serializer: Any, raise_exception: bool) -> bool:
    # An ``is_valid()`` written for DRF. When it calls DRF's, async
    # validation would be called and never awaited: the coroutine would end
    # up in ``validated_data`` and its errors would be lost.
    cls = type(serializer)
    if not _bridged(cls, "is_valid") and validation_kind(serializer) is Kind.ASYNC:
        raise ImproperlyConfigured(
            f"{cls.__qualname__} overrides is_valid() of DRF, which does not await "
            "its async validation. Override `async def ais_valid()` instead, or "
            "inherit from an aiodrf serializer, whose is_valid() awaits it."
        )
    return serializer.is_valid(raise_exception=raise_exception)


def _try_default_is_valid(serializer: Any, raise_exception: bool) -> Any:
    if validation_kind(serializer) is Kind.ASYNC:
        return NEEDS_AWAIT
    return _sync_is_valid(serializer, raise_exception)


def _sync_is_valid(serializer: Any, raise_exception: bool) -> bool:
    backend = _input_backend(serializer)
    # The python backend compiles output only: DRF validates.
    if not hasattr(serializer, "_validated_data") and backend not in ("drf", "python"):
        from fastdrf.inputs import NOT_RECOGNIZED, recognize

        value = recognize(serializer, backend=backend)
        if value is not NOT_RECOGNIZED:
            # The state DRF's ``is_valid()`` leaves for valid input.
            serializer._validated_data = value
            serializer._errors = (
                [] if isinstance(serializer, serializers.ListSerializer) else {}
            )
            return True
    batched = _batch_related_lookups(serializer)
    try:
        return _sync_member(serializer, "is_valid", "ais_valid")(
            raise_exception=raise_exception
        )
    finally:
        _unbatch(batched)


def _input_backend(serializer: Any) -> str:
    target = (
        serializer.child
        if isinstance(serializer, serializers.ListSerializer)
        else serializer
    )
    backend = getattr(getattr(target, "Meta", None), "serializer_backend", None)
    return backend or fastdrf_settings.SERIALIZER_BACKEND


def _finish_is_valid(serializer: Any, raise_exception: bool) -> bool:
    if serializer._errors and raise_exception:
        raise ValidationError(serializer.errors)
    return not bool(serializer._errors)


async def run_validation(serializer: Any, data: Any = fields.empty) -> Any:
    """Async counterpart of ``serializer.run_validation()``."""
    impl = resolve_pair(serializer, "run_validation", "arun_validation")
    if impl is Impl.ASYNC:
        return await _acall(serializer.arun_validation, data)
    if impl is Impl.SYNC_IS_ASYNC:
        return await _acall(serializer.run_validation, data)
    return await default_run_validation(serializer, data)


async def default_run_validation(serializer: Any, data: Any = fields.empty) -> Any:
    """The implementation behind :func:`run_validation` and ``arun_validation()``."""
    if not _classifies_inline(serializer):
        # A direct caller: the fields are built in the worker. A parent
        # walking its nested serializers classified them already.
        await run_sync(validation_kind)(serializer)
    kind = validation_kind(serializer)
    if kind is not Kind.ASYNC:
        method = _sync_member(serializer, "run_validation", "arun_validation")
        return await _call_sync(method, kind, data)

    # A default or an override may query: see ``empty_values_kind``.
    (is_empty_value, data) = await _call_sync(
        serializer.validate_empty_values, empty_values_kind(serializer), data
    )
    if is_empty_value:
        return data

    if isinstance(serializer, serializers.ListSerializer):
        value = await _alist_to_internal_value(serializer, data)
    else:
        value = await _ato_internal_value(serializer, data)
    try:
        await _arun_validators(serializer, value)
        value = await _acall_hook(serializer, "validate", "avalidate", value)
        assert value is not None, (  # noqa: S101 -- as in DRF
            ".validate() should return the validated data"
        )
    except (ValidationError, DjangoValidationError) as exc:
        raise ValidationError(detail=as_serializer_error(exc))

    return value


async def _call_sync(func: Any, kind: Any, /, *args: Any, **kwargs: Any) -> Any:
    if _resolve_unknown(kind) is Kind.PURE:
        return func(*args, **kwargs)
    return await run_sync(func)(*args, **kwargs)


async def _acall_hook(
    serializer: Any, sync_name: str, async_name: str, value: Any
) -> Any:
    hook, kind = _hook(serializer, sync_name, async_name)
    if hook is None:
        return value
    if kind is Kind.ASYNC:
        return await _acall(hook, value)
    return await _call_sync(hook, kind, value)


class _FieldRun:
    """One writable field on its way through the stages below."""

    __slots__ = ("failure", "field", "reached_validators", "value")

    def __init__(self, field: Any) -> None:
        self.field = field
        self.value: Any = None
        self.reached_validators = False
        self.failure: Exception | None = None


_FIELD_FAILURES = (ValidationError, DjangoValidationError, SkipField)


async def _ato_internal_value(serializer: Any, data: Any) -> Any:
    """
    ``Serializer.to_internal_value`` for serializers with async hooks.

    DRF runs, field after field: the field's conversion, its validators,
    then ``validate_<field>``. Those are the *stages* here, in the same
    order. Consecutive synchronous stages run together, in one thread hop if
    any of them needs a thread, and async stages are awaited in between, so
    user code observes DRF's order whatever mix of hooks a serializer has.
    """
    impl = resolve_pair(serializer, "to_internal_value", "ato_internal_value")
    if impl in (Impl.ASYNC, Impl.SYNC_IS_ASYNC):
        method = (
            serializer.ato_internal_value
            if impl is Impl.ASYNC
            else serializer.to_internal_value
        )
        return await _acall(method, data)

    if not isinstance(data, Mapping):
        message = serializer.error_messages["invalid"].format(
            datatype=type(data).__name__
        )
        raise ValidationError(
            {api_settings.NON_FIELD_ERRORS_KEY: [message]}, code="invalid"
        )

    runs = [_FieldRun(field) for field in serializer._writable_fields]
    pending = []
    for kind, stage, run in _stages(serializer, runs):
        if kind is not Kind.ASYNC:
            pending.append((kind, stage, run))
            continue
        await _flush(pending, data)
        pending = []
        if run.failure is None:
            try:
                await stage(run, data)
            except _FIELD_FAILURES as exc:
                run.failure = exc
    await _flush(pending, data)

    ret: dict[str, Any] = {}
    errors: dict[str, Any] = {}
    for run in runs:
        failure = run.failure
        if failure is None:
            serializer.set_value(ret, run.field.source_attrs, run.value)
        elif isinstance(failure, ValidationError):
            errors[run.field.field_name] = failure.detail
        elif isinstance(failure, DjangoValidationError):
            errors[run.field.field_name] = get_error_detail(failure)
        # SkipField: the field is left out.

    if errors:
        raise ValidationError(errors)

    return ret


def _stages(
    serializer: Any, runs: Any
) -> Iterator[tuple[Kind, Callable[..., Any], _FieldRun]]:
    """Yield ``(kind, stage, run)`` for every stage of every field, in DRF's order."""
    for run in runs:
        field = run.field
        if isinstance(field, serializers.BaseSerializer):
            if validation_kind(field) is Kind.ASYNC:
                yield Kind.ASYNC, _anested, run
            else:
                # With its default, which DRF calls for a missing value.
                yield _resolve_unknown(_field_value_kind(field)), _validate, run
        elif _conversion_kind(field) is Kind.ASYNC:
            yield Kind.ASYNC, _acollection, run
        elif _validators_kind(field.validators) is Kind.ASYNC:
            yield _resolve_unknown(_conversion_kind(field)), _convert, run
            yield Kind.ASYNC, _avalidate, run
        else:
            yield _resolve_unknown(_field_value_kind(field)), _validate, run

        name = field.field_name
        hook, kind = _hook(serializer, f"validate_{name}", f"avalidate_{name}")
        if hook is not None and kind is not None:  # both, or neither
            yield _resolve_unknown(kind), _hook_stage(hook, kind), run


async def _flush(stages: Any, data: Any) -> None:
    if not stages:
        return
    if any(kind is Kind.IMPURE for kind, _, _ in stages):
        await run_sync(_run_stages)(stages, data)
    else:
        _run_stages(stages, data)


def _run_stages(stages: Any, data: Any) -> None:
    for _, stage, run in stages:
        # A field stops at its first failure, like the ``try`` block around
        # a field in DRF's loop.
        if run.failure is None:
            try:
                stage(run, data)
            except _FIELD_FAILURES as exc:
                run.failure = exc


def _validate(run: Any, data: Any) -> None:
    run.value = run.field.run_validation(run.field.get_value(data))


def _convert(run: Any, data: Any) -> None:
    """
    ``field.run_validation()`` up to, but not including, its validators.

    Preserve the standard Field and CharField empty/default rules without
    mutating field methods. Custom validation wrappers are rejected when
    they would invoke asynchronous validators synchronously.
    """
    run.value, run.reached_validators = _convert_value(
        run.field, run.field.get_value(data)
    )


def _convert_value(field: Any, data: Any, *, collection: bool = False) -> Any:
    """Keep DRF's empty/default/conversion rules; defer only awaited stages."""
    method = getattr(field.run_validation, "__func__", None)
    validators_method = getattr(field.run_validators, "__func__", None)
    if (
        method
        not in (
            fields.Field.run_validation,
            fields.CharField.run_validation,
            relations.RelatedField.run_validation,
        )
        or validators_method is not fields.Field.run_validators
    ):
        raise ImproperlyConfigured(
            "Async field validators cannot be combined with custom run_validation() "
            "or run_validators(). Put the async work in validate_<field>() instead."
        )
    if method is fields.CharField.run_validation and (
        data == "" or (field.trim_whitespace and str(data).strip() == "")
    ):
        if not field.allow_blank:
            field.fail("blank")
        return "", False
    if method is relations.RelatedField.run_validation and data == "":
        data = None
    is_empty, value = field.validate_empty_values(data)
    if is_empty:
        return value, False
    if collection and type(field) is fields.ListField:
        if html.is_html_input(value):
            value = html.parse_html_list(value, default=[])
        if isinstance(value, (str, Mapping)) or not hasattr(value, "__iter__"):
            field.fail("not_a_list", input_type=type(value).__name__)
        if not field.allow_empty and len(value) == 0:
            field.fail("empty")
    elif collection and type(field) is fields.DictField:
        if html.is_html_input(value):
            value = html.parse_html_dict(value)
        if not isinstance(value, dict):
            field.fail("not_a_dict", input_type=type(value).__name__)
        if not field.allow_empty and len(value) == 0:
            field.fail("empty")
    else:
        value = field.to_internal_value(value)
    return value, True


async def _acollection(run: Any, data: Any) -> None:
    value = await run_sync(run.field.get_value)(data)
    run.value = await _arun_field(run.field, value)


async def _arun_field(field: Any, data: Any) -> Any:
    if isinstance(field, serializers.BaseSerializer):
        return await run_validation(field, data)
    kind = _field_value_kind(field)
    if kind is not Kind.ASYNC:
        return await _call_sync(field.run_validation, kind, data)

    collection = _conversion_kind(field) is Kind.ASYNC
    convert_kind = _conversion_kind(field, children=False)
    # Only stock collection fields reach this branch: custom conversion may
    # depend on already validated children and cannot be split safely.
    value, reached = await _call_sync(
        _convert_value, convert_kind, field, data, collection=collection
    )
    if not reached:
        return value
    if collection:
        is_dict = type(field) is fields.DictField
        mapping: dict[Any, Any] = {}
        sequence: list[Any] = []
        errors: dict[Any, Any] = {}
        items = value.items() if is_dict else enumerate(value)
        for raw_key, item in items:
            key = str(raw_key) if is_dict else raw_key
            try:
                converted = await _arun_field(field.child, item)
            except ValidationError as exc:
                errors[key] = exc.detail
            except DjangoValidationError as exc:
                if is_dict:
                    raise
                errors[key] = get_error_detail(exc)
            else:
                if is_dict:
                    mapping[key] = converted
                else:
                    sequence.append(converted)
        if errors:
            raise ValidationError(errors)
        value = mapping if is_dict else sequence
    await _arun_field_validators(field, value)
    return value


async def _avalidate(run: Any, data: Any) -> None:
    if run.reached_validators:
        await _arun_field_validators(run.field, run.value)


async def _anested(run: Any, data: Any) -> None:
    field = run.field
    value = await _call_sync(field.get_value, empty_values_kind(field), data)
    run.value = await run_validation(field, value)


def _hook_stage(hook: Any, kind: Any) -> Callable[..., Any]:
    async def awaited(run: Any, data: Any) -> None:
        run.value = await _acall(hook, run.value)

    def called(run: Any, data: Any) -> None:
        run.value = hook(run.value)

    return awaited if kind is Kind.ASYNC else called


async def _arun_field_validators(field: Any, value: Any) -> None:
    """Mirrors ``Field.run_validators``, awaiting async validators."""
    errors: list[Any] = []
    for validator in field.validators:
        try:
            if getattr(validator, "requires_context", False):
                await _acall_validator(validator, value, field)
            else:
                await _acall_validator(validator, value)
        except ValidationError as exc:
            # If the validation error contains a mapping of fields to
            # errors then simply raise it immediately rather than
            # attempting to accumulate a list of errors.
            if isinstance(exc.detail, dict):
                raise
            errors.extend(exc.detail)
        except DjangoValidationError as exc:
            errors.extend(get_error_detail(exc))
    if errors:
        raise ValidationError(errors)


async def _acall_validator(validator: Any, *args: Any) -> Any:
    kind = _resolve_unknown(_validator_kind(validator))
    if kind is Kind.ASYNC:
        return await _acall(validator, *args)
    if kind is Kind.PURE:
        return validator(*args)
    return await run_sync(validator)(*args)


async def _arun_validators(serializer: Any, value: Any) -> Any:
    impl = resolve_pair(serializer, "run_validators", "arun_validators")
    if impl in (Impl.ASYNC, Impl.SYNC_IS_ASYNC):
        method = (
            serializer.arun_validators
            if impl is Impl.ASYNC
            else serializer.run_validators
        )
        await _acall(method, value)
        return
    kind = _validators_kind(serializer.validators)
    # ``Serializer.run_validators`` calls the defaults of read-only fields.
    defaults = isinstance(value, dict) and isinstance(
        serializer, serializers.Serializer
    )
    defaults_kind = read_only_defaults_kind(serializer) if defaults else Kind.PURE
    if kind is not Kind.ASYNC:
        await _call_sync(serializer.run_validators, max(kind, defaults_kind), value)
        return

    # Mirrors ``Serializer.run_validators``: read-only fields with defaults
    # take part in validation.
    if defaults:
        to_validate = await _call_sync(serializer._read_only_defaults, defaults_kind)
        to_validate.update(value)
    else:
        to_validate = value
    await _arun_field_validators(serializer, to_validate)


async def _alist_to_internal_value(serializer: Any, data: Any) -> Any:
    """Mirrors ``ListSerializer.to_internal_value``."""
    impl = resolve_pair(serializer, "to_internal_value", "ato_internal_value")
    if impl in (Impl.ASYNC, Impl.SYNC_IS_ASYNC):
        method = (
            serializer.ato_internal_value
            if impl is Impl.ASYNC
            else serializer.to_internal_value
        )
        return await _acall(method, data)

    if html.is_html_input(data):
        data = html.parse_html_list(data, default=[])

    if not isinstance(data, list):
        message = serializer.error_messages["not_a_list"].format(
            input_type=type(data).__name__
        )
        raise ValidationError(
            {api_settings.NON_FIELD_ERRORS_KEY: [message]}, code="not_a_list"
        )

    if not serializer.allow_empty and len(data) == 0:
        message = serializer.error_messages["empty"]
        raise ValidationError(
            {api_settings.NON_FIELD_ERRORS_KEY: [message]}, code="empty"
        )

    max_length = getattr(serializer, "max_length", None)
    if max_length is not None and len(data) > max_length:
        message = serializer.error_messages["max_length"].format(max_length=max_length)
        raise ValidationError(
            {api_settings.NON_FIELD_ERRORS_KEY: [message]}, code="max_length"
        )

    min_length = getattr(serializer, "min_length", None)
    if min_length is not None and len(data) < min_length:
        message = serializer.error_messages["min_length"].format(min_length=min_length)
        raise ValidationError(
            {api_settings.NON_FIELD_ERRORS_KEY: [message]}, code="min_length"
        )

    ret = []
    errors: dict[Any, Any] = {}

    for index, item in enumerate(data):
        try:
            validated = await _arun_child_validation(serializer, item)
        except ValidationError as exc:
            errors[index] = exc.detail
        else:
            ret.append(validated)

    if errors:
        raise ValidationError(_list_errors(errors, len(data)))

    return ret


async def _arun_child_validation(serializer: Any, item: Any) -> Any:
    impl = resolve_pair(serializer, "run_child_validation", "arun_child_validation")
    if impl is Impl.ASYNC:
        return await _acall(serializer.arun_child_validation, item)
    if impl is Impl.SYNC_IS_ASYNC:
        return await _acall(serializer.run_child_validation, item)
    return await run_validation(serializer.child, item)


def _list_errors(errors: Any, length: Any) -> Any:
    """
    Format ``ListSerializer`` errors like the installed DRF version does:
    DRF 3.18 added ``LIST_SERIALIZER_ERRORS_AS_DICT`` and deprecated the list
    format, which is the only format of earlier versions.
    """
    as_dict = (
        api_settings.LIST_SERIALIZER_ERRORS_AS_DICT
        if DRF_HAS_LIST_ERRORS_AS_DICT
        else None
    )
    if as_dict:
        return errors
    if as_dict is not None:
        from rest_framework.deprecation import RemovedInDRF320Warning

        warnings.warn(
            "The list-based error format for `ListSerializer` is "
            "deprecated and will be removed in DRF 3.20. Set "
            '`REST_FRAMEWORK["LIST_SERIALIZER_ERRORS_AS_DICT"]` to '
            "`True` to use the dictionary-based error format.",
            RemovedInDRF320Warning,
            stacklevel=4,
        )
    return [errors.get(index, {}) for index in range(length)]
