"""Loaded output is classified once without changing async override behavior."""

from unittest.mock import patch

import pytest
from django.test import override_settings
from rest_framework import serializers

from aiodrf import aio
from aiodrf.aio import _represent
from aiodrf.test import count_hops
from tests.testapp.models import Author


@pytest.mark.parametrize("backend", ["drf", "msgspec", "pydantic"])
@pytest.mark.parametrize("many", [False, True])
async def test_loaded_representation_does_not_repeat_async_classification(
    backend, many
):
    class Data(serializers.ModelSerializer):
        class Meta:
            model = Author
            fields = ["id", "name"]

    author = Author(pk=1, name="Ada")
    value = [author] if many else author
    expected = [{"id": 1, "name": "Ada"}] if many else {"id": 1, "name": "Ada"}
    with override_settings(
        AIODRF={"REPRESENTATION_MODE": "inline"},
        FASTDRF={"SERIALIZER_BACKEND": backend, "SERIALIZER_BACKEND_FALLBACK": "error"},
    ):
        assert await aio.data(Data(value, many=many)) == expected
        with (
            patch.object(
                _represent,
                "has_async_representation",
                wraps=_represent.has_async_representation,
            ) as classify,
            count_hops() as hops,
        ):
            assert await aio.data(Data(value, many=many)) == expected
        assert classify.call_count == (1 if backend == "drf" else 0)
        assert hops.count == 0


@pytest.mark.parametrize("backend", ["drf", "msgspec", "pydantic"])
@pytest.mark.parametrize("lazy", [False, True])
async def test_async_representation_keeps_the_override_without_evaluating_source(
    backend, lazy
):
    calls = []

    class Data(serializers.Serializer):
        async def ato_representation(self, instance):
            calls.append(instance)
            return {"redacted": True}

    source = Author.objects.all() if lazy else {"secret": 1}
    with (
        override_settings(
            AIODRF={"REPRESENTATION_MODE": "inline"},
            FASTDRF={
                "SERIALIZER_BACKEND": backend,
                "SERIALIZER_BACKEND_FALLBACK": "error",
            },
        ),
        count_hops() as hops,
    ):
        assert await aio.data(Data(source)) == {"redacted": True}
    assert calls == [source]
    assert hops.count == 0
    if lazy:
        assert source._result_cache is None
