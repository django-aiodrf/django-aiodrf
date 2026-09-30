import asyncio
import functools
import inspect
import threading
from types import SimpleNamespace

import pytest
from django.test import SimpleTestCase
from django.utils.asyncio import async_unsafe
from rest_framework import fields

from aiodrf import aio
from aiodrf.aio._common import _bridged, _sync_member
from aiodrf.aio._represent import _afield_representation
from aiodrf.permissions import BasePermission as AsyncBasePermission
from aiodrf.policies import ahas_object_permission, ahas_permission
from aiodrf.serializers import Serializer
from aiodrf.utils import call_pair, class_cache, run_sync


def wrapped_async(func):
    @functools.wraps(func)
    @async_unsafe
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)

    return wrapper


class BridgeRegressionTests(SimpleTestCase):
    async def test_reverse_bridge_on_loop_does_not_run_sync_prefix(self):
        entered = []

        def wrap(func):
            @functools.wraps(func)
            def prefix(*args, **kwargs):
                entered.append(True)
                return func(*args, **kwargs)

            return prefix

        class Policy(AsyncBasePermission):
            @wrap
            async def ahas_permission(self, request, view):
                return False

        with self.assertRaisesMessage(RuntimeError, "was called on the event loop"):
            Policy().has_permission(None, None)
        assert entered == []

    async def test_related_field_async_validator_keeps_empty_string_null_rule(self):
        from rest_framework.relations import PrimaryKeyRelatedField

        async def accepts(value):
            raise AssertionError("Null values skip validators")

        class Data(Serializer):
            owner = PrimaryKeyRelatedField(
                queryset=(), allow_null=True, validators=[accepts]
            )

        serializer = Data(data={"owner": ""})
        assert await serializer.ais_valid()
        assert serializer.validated_data == {"owner": None}

    async def test_instance_async_permission_is_enforced_in_both_directions(self):
        policy = AsyncBasePermission()

        async def deny(request, view):
            return False

        policy.ahas_permission = deny
        assert (
            await call_pair(policy, "has_permission", "ahas_permission", None, None)
            is False
        )
        assert await run_sync(policy.has_permission)(None, None) is False

    async def test_wrapped_async_companion_prefix_stays_in_worker(self):
        class Policy(AsyncBasePermission):
            @wrapped_async
            async def ahas_permission(self, request, view):
                return False

        policy = Policy()
        assert (
            await call_pair(policy, "has_permission", "ahas_permission", None, None)
            is False
        )
        assert await run_sync(policy.has_permission)(None, None) is False

    async def test_slotted_policy_does_not_need_an_instance_dictionary(self):
        class Policy:
            __slots__ = ()

            async def ahas_permission(self, request, view):
                return False

        assert (
            await call_pair(Policy(), "has_permission", "ahas_permission", None, None)
            is False
        )

    async def test_sync_policy_returned_coroutine_is_awaited(self):
        class Policy:
            @async_unsafe
            def has_permission(self, request, view):
                async def deny():
                    return False

                return deny()

        result = await call_pair(
            Policy(), "has_permission", "ahas_permission", None, None
        )
        if inspect.iscoroutine(result):
            result.close()
        assert result is False

    @pytest.mark.aiodrf_settings(REPRESENTATION_MODE="thread")
    async def test_instance_sync_representation_overrides_async_parent(self):
        class Data(Serializer):
            secret = fields.CharField()

            async def ato_representation(self, instance):
                raise AssertionError("The instance override must win")

        serializer = Data({"secret": "private"})

        @async_unsafe
        def redact(instance):
            return {"secret": "redacted"}

        serializer.to_representation = redact
        assert (
            _sync_member(serializer, "to_representation", "ato_representation")
            is redact
        )
        assert await aio.to_representation(serializer, serializer.instance) == {
            "secret": "redacted"
        }

    def test_perform_dispatch_respects_instance_async_override(self):
        from aiodrf.mixins import CreateModelMixin, _perform

        class View(CreateModelMixin):
            def perform_create(self, serializer):
                raise AssertionError("The instance async hook must win")

        async def perform(serializer):
            raise AssertionError("Only classified here, not run")

        view = View()
        view.aperform_create = perform
        assert (
            _perform(view, "perform_create", "aperform_create", object())
            is aio.NEEDS_AWAIT
        )

    def test_serializer_bridge_marker_is_method_specific(self):
        assert _bridged(Serializer, "save")
        assert not _bridged(Serializer, "not_a_serializer_operation")

    @pytest.mark.aiodrf_settings(REPRESENTATION_MODE="thread")
    async def test_async_source_conversion_does_not_trust_field_module_name(self):
        class Field(fields.CharField):
            __module__ = "rest_framework.fields"

            @async_unsafe
            def to_representation(self, value):
                return super().to_representation(value)

        field = Field()
        field.bind("value", None)

        async def value():
            return "safe"

        assert (
            await _afield_representation(field, SimpleNamespace(value=value)) == "safe"
        )

    @pytest.mark.aiodrf_settings(REPRESENTATION_MODE="thread")
    async def test_async_source_conversion_honors_instance_field_hook(self):
        field = fields.CharField()
        field.bind("value", None)

        @async_unsafe
        def redact(value):
            return "redacted"

        field.to_representation = redact

        async def value():
            return "private"

        assert (
            await _afield_representation(field, SimpleNamespace(value=value))
            == "redacted"
        )

    def test_cache_clear_during_computation_does_not_publish_old_value(self):
        entered = threading.Event()
        resume = threading.Event()
        state = ["old"]

        class Target:
            pass

        @class_cache
        def metadata(cls):
            value = state[0]
            if value == "old":
                entered.set()
                assert resume.wait(5)
            return value

        values = []
        worker = threading.Thread(target=lambda: values.append(metadata(Target)))
        worker.start()
        try:
            assert entered.wait(5)
            state[0] = "new"
            metadata.cache_clear()
        finally:
            resume.set()
            worker.join(5)
        assert not worker.is_alive()
        assert values == ["old"]
        assert metadata(Target) == "new"

    async def test_returned_coroutine_is_closed_when_worker_is_cancelled(self):
        entered = threading.Event()
        release = threading.Event()
        finished = threading.Event()
        returned = []

        class Policy:
            def has_permission(self, request, view):
                entered.set()
                assert release.wait(5)

                async def result():
                    raise AssertionError("Cancelled work must not start")

                coroutine = result()
                returned.append(coroutine)
                finished.set()
                return coroutine

        task = asyncio.create_task(
            call_pair(Policy(), "has_permission", "ahas_permission", None, None)
        )
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            # Let cancellation reach the worker boundary before releasing it.
            await asyncio.sleep(0)
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert await asyncio.to_thread(finished.wait, 5)
            await run_sync(lambda: None)()
            assert inspect.getcoroutinestate(returned[0]) == inspect.CORO_CLOSED
        finally:
            release.set()
            if not task.done():
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            for coroutine in returned:
                coroutine.close()


class PermissionOperatorRegressionTests(SimpleTestCase):
    async def test_sync_policy_returning_coroutine_is_denied_in_worker_mode(self):
        from aiodrf.policies import has_permission

        class Policy:
            @async_unsafe
            def has_permission(self, request, view):
                async def deny():
                    return False

                return deny()

        assert await run_sync(has_permission)(Policy(), None, None) is False

    async def test_custom_operator_is_not_replaced_by_stock_boolean_logic(self):
        from rest_framework.permissions import AND, AllowAny

        class Deny(AND):
            @async_unsafe
            def has_permission(self, request, view):
                return False

            @async_unsafe
            def has_object_permission(self, request, view, obj):
                return False

        permission = Deny(AllowAny(), AllowAny())
        assert await ahas_permission(permission, None, None) is False
        assert await ahas_object_permission(permission, None, None, object()) is False

    async def test_or_object_permission_rechecks_each_global_permission(self):
        from rest_framework.permissions import OR

        events = []

        class Left(AsyncBasePermission):
            async def ahas_permission(self, request, view):
                events.append("left-global")
                return False

            async def ahas_object_permission(self, request, view, obj):
                raise AssertionError("Global denial must short-circuit this branch")

        class Right(AsyncBasePermission):
            async def ahas_permission(self, request, view):
                events.append("right-global")
                return True

            async def ahas_object_permission(self, request, view, obj):
                events.append("right-object")
                return False

        permission = OR(Left(), Right())
        assert await ahas_object_permission(permission, None, None, object()) is False
        assert events == ["left-global", "right-global", "right-object"]

    async def test_builtin_boolean_operators_preserve_short_circuit_order(self):
        from rest_framework.permissions import AND, NOT, OR

        class Deny(AsyncBasePermission):
            async def ahas_permission(self, request, view):
                return False

        class Unreachable(AsyncBasePermission):
            async def ahas_permission(self, request, view):
                raise AssertionError("Must short-circuit")

        assert await ahas_permission(AND(Deny(), Unreachable()), None, None) is False
        assert await ahas_permission(OR(NOT(Deny()), Unreachable()), None, None) is True


class SerializerBoundaryBackportTests(SimpleTestCase):
    @pytest.mark.aiodrf_settings(
        REPRESENTATION_MODE="thread", SERIALIZER_BACKEND_FALLBACK="drf"
    )
    async def test_custom_data_property_stays_in_worker_after_cache_hit(self):
        class Data(Serializer):
            value = fields.IntegerField()

            @property
            @async_unsafe
            def data(self):
                return super().data

        serializer = Data({"value": 1})
        assert await aio.data(serializer) == {"value": 1}
        assert await aio.data(serializer) == {"value": 1}

    @pytest.mark.aiodrf_settings(SERIALIZER_BACKEND_FALLBACK="drf")
    async def test_custom_data_hook_can_call_super_once(self):
        class Data(Serializer):
            value = fields.IntegerField()

            async def adata(self):
                result = await super().adata()
                return dict(result, extra=True)

        assert await aio.data(Data({"value": 1})) == {"value": 1, "extra": True}

    async def test_instance_validation_and_representation_hooks(self):
        class Data(Serializer):
            secret = fields.CharField()

        async def redacted(instance):
            return {"secret": "redacted"}

        serializer = Data({"secret": "private"})
        serializer.ato_representation = redacted
        assert await aio.data(serializer) == {"secret": "redacted"}

        @async_unsafe
        def validate(**kwargs):
            return "custom"

        serializer = Data(data={})
        serializer.is_valid = validate
        assert await aio.is_valid(serializer) == "custom"

    async def test_instance_create_hook(self):
        class Data(Serializer):
            value = fields.IntegerField()

        async def create(data):
            return dict(data, created=True)

        serializer = Data(data={"value": 1})
        serializer.acreate = create
        assert await serializer.ais_valid()
        assert await serializer.asave() == {"value": 1, "created": True}

    async def test_wrapped_async_write_calls_prefix_in_worker(self):
        class Data(Serializer):
            value = fields.IntegerField()

            @wrapped_async
            async def acreate(self, data):
                return data

        serializer = Data(data={"value": 1})
        assert await serializer.ais_valid()
        assert await serializer.asave() == {"value": 1}

    async def test_async_field_conversion_fails_before_call(self):
        from django.core.exceptions import ImproperlyConfigured

        class Field(fields.IntegerField):
            async def to_internal_value(self, value):
                raise AssertionError("Must not be called")

        class Data(Serializer):
            value = Field()

        with self.assertRaisesMessage(ImproperlyConfigured, "must be synchronous"):
            await Data(data={"value": 1}).ais_valid()

    async def test_async_default_fails_before_call(self):
        from django.core.exceptions import ImproperlyConfigured

        async def default():
            raise AssertionError("Must not be called")

        class Data(Serializer):
            value = fields.IntegerField(default=default)

        with self.assertRaisesMessage(
            ImproperlyConfigured, "defaults must be synchronous"
        ):
            await Data(data={}).ais_valid()

    async def test_validator_namespace_does_not_assert_loop_safety(self):
        class Validator:
            __module__ = "django.core.validators"

            @async_unsafe
            def __call__(self, value):
                return None

        class Data(Serializer):
            value = fields.IntegerField(validators=[Validator()])

        assert await Data(data={"value": 1}).ais_valid()

    async def test_conversion_does_not_replace_field_validator_method(self):
        from django.core.exceptions import ImproperlyConfigured

        async def validate(value):
            return None

        class Field(fields.IntegerField):
            def run_validation(self, value):
                assert "run_validators" not in vars(self)
                return super().run_validation(value)

        class Data(Serializer):
            value = Field(validators=[validate])

        with self.assertRaisesMessage(ImproperlyConfigured, "run_validation"):
            await Data(data={"value": 1}).ais_valid()
