"""Request isolation and generated field configurations, compared with DRF."""

import asyncio
import gc
import weakref

import pytest
from django.core.validators import MaxValueValidator, MinValueValidator
from django.http import QueryDict
from django.test import override_settings
from hypothesis import given, settings
from hypothesis import strategies as st
from rest_framework import serializers

from aiodrf import aio
from aiodrf.contrib import compiler, inputs
from tests.test_inputs import exact


@pytest.mark.parametrize("shape", ["root", "nested", "many", "list", "dict"])
async def test_dynamic_async_validation_is_isolated_between_concurrent_requests(shape):
    waiting = []
    ready = asyncio.Event()

    class Base(serializers.Serializer):
        value = serializers.IntegerField()

        async def validate(self, attrs):
            waiting.append(self.context["tenant"])
            if len(waiting) == 8:
                ready.set()
            await ready.wait()
            if self.context["tenant"] % 2:
                raise serializers.ValidationError("denied", code="tenant")
            return attrs

    class Child(Base):
        async def validate(self, attrs):
            return await super().validate(attrs)

    fields = {
        "nested": Child,
        "many": lambda: Child(many=True),
        "list": lambda: serializers.ListField(child=Child()),
        "dict": lambda: serializers.DictField(child=Child()),
    }
    if shape != "root":
        parent = type("Parent", (serializers.Serializer,), {"child": fields[shape]()})

    def instance(tenant):
        value = {"value": tenant}
        if shape == "root":
            return Child(data=value, context={"tenant": tenant})
        data = (
            value
            if shape == "nested"
            else {"key": value}
            if shape == "dict"
            else [value]
        )
        return parent(data={"child": data}, context={"tenant": tenant})

    async def validate(tenant):
        candidate = instance(tenant)
        valid = await aio.is_valid(candidate)
        assert valid == (tenant % 2 == 0)
        assert bool(candidate.errors) is not valid

    await asyncio.wait_for(asyncio.gather(*(validate(n) for n in range(8))), 3)
    assert sorted(waiting) == list(range(8))


@pytest.mark.parametrize("backend", ["msgspec", "pydantic"])
def test_compiled_input_does_not_retain_serializer_context(backend):
    class Tenant:
        pass

    class Input(serializers.Serializer):
        value = serializers.IntegerField()

    tenant = Tenant()
    reference = weakref.ref(tenant)
    serializer = Input(data={"value": 1}, context={"tenant": tenant})
    assert inputs.recognize(serializer, backend=backend) == {"value": 1}
    del serializer, tenant
    gc.collect()
    assert reference() is None


@pytest.mark.parametrize("backend", ["msgspec", "pydantic"])
@settings(derandomize=True, database=None, deadline=None, max_examples=120)
@given(
    required=st.booleans(),
    nullable=st.booleans(),
    blank=st.booleans(),
    trim=st.booleans(),
    partial=st.booleans(),
    default=st.sampled_from([True, 1, 1.0]),
    payload=st.dictionaries(
        st.sampled_from(["name", "count", "ignored"]),
        st.one_of(
            st.none(),
            st.booleans(),
            st.integers(-2, 5),
            st.floats(),
            st.text(max_size=8),
        ),
        max_size=3,
    ),
)
def test_generated_flags_sources_and_defaults_match_drf(
    backend, required, nullable, blank, trim, partial, default, payload
):
    class Input(serializers.Serializer):
        name = serializers.CharField(
            required=required,
            allow_null=nullable,
            allow_blank=blank,
            trim_whitespace=trim,
            source="nested.name",
            write_only=True,
        )
        count = serializers.IntegerField(default=default, allow_null=nullable)
        ignored = serializers.CharField(read_only=True)

    serializer = Input(data=payload, partial=partial)
    recognized = inputs.recognize(serializer, backend=backend)
    reference = Input(data=payload, partial=partial)
    if recognized is not inputs.NOT_RECOGNIZED:
        assert reference.is_valid(), reference.errors
        assert exact(recognized) == exact(reference.validated_data)


@pytest.mark.parametrize("backend", ["msgspec", "pydantic"])
@pytest.mark.parametrize("fallback", ["drf", "error"])
async def test_form_repeated_keys_and_callable_defaults_use_drf(backend, fallback):
    calls = []

    class Input(serializers.Serializer):
        name = serializers.CharField()
        count = serializers.IntegerField(default=lambda: calls.append("default") or 3)

    data = QueryDict("name=first&name=last")
    reference = Input(data=data)
    assert reference.is_valid()
    calls.clear()
    with override_settings(
        AIODRF={"SERIALIZER_BACKEND": backend, "SERIALIZER_BACKEND_FALLBACK": fallback}
    ):
        candidate = Input(data=data)
        assert await aio.is_valid(candidate)
        assert candidate.validated_data == reference.validated_data
    assert calls == ["default"]


def test_diagnostic_codes_do_not_depend_on_names_or_request_data():
    class Input(serializers.Serializer):
        value = serializers.IntegerField(default=lambda: 1)

    candidate = Input(context={"secret": "never include this"})
    report = inputs.report_input_details(candidate)
    assert report.code == "dynamic_default"
    assert "secret" not in report.reason
    assert not report.eligible
    assert compiler.report_details(candidate).code == "model_required"


@pytest.mark.parametrize("backend", ["msgspec", "pydantic"])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("minimum", [0, 8])
def test_repeated_maximum_and_contradictory_bounds(backend, reverse, minimum):
    validators = [
        MaxValueValidator(7),
        MaxValueValidator(3),
        MinValueValidator(minimum),
    ]

    class Input(serializers.Serializer):
        value = serializers.IntegerField(
            validators=validators[::-1] if reverse else validators
        )

    for value in range(-1, 10):
        serializer = Input(data={"value": value})
        recognized = inputs.recognize(serializer, backend=backend)
        if recognized is not inputs.NOT_RECOGNIZED:
            assert serializer.is_valid(), serializer.errors
            assert exact(recognized) == exact(serializer.validated_data)
