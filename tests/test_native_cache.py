"""Cache codec interoperability with django-fastdrf."""

from datetime import UTC

import pytest
from aiodrf_async_cache.redis import AsyncRedisCache


def _codecs():
    # aiodrf's MsgspecCodec (MessagePack integers) and django-fastdrf's
    # PydanticCodec, by name.
    from types import SimpleNamespace

    from aiodrf_async_cache.codecs import MsgspecCodec
    from fastdrf.codecs import PydanticCodec

    return SimpleNamespace(MsgspecCodec=MsgspecCodec, PydanticCodec=PydanticCodec)


@pytest.mark.parametrize("codec", ["MsgspecCodec", "PydanticCodec"])
async def test_typed_codecs_roundtrip_without_pickle(codec):
    from datetime import datetime
    from uuid import UUID, uuid4

    cache_codecs = _codecs()

    value = {"id": uuid4(), "created": datetime(2026, 1, 1, tzinfo=UTC)}
    if codec == "MsgspecCodec":
        import msgspec

        class Schema(msgspec.Struct):
            id: UUID
            created: datetime
    else:
        from pydantic import BaseModel

        class Schema(BaseModel):
            id: UUID
            created: datetime

    serializer = getattr(cache_codecs, codec)(Schema)
    cache = AsyncRedisCache(
        "redis://localhost", {"OPTIONS": {"serializer": serializer}}
    )
    encoded = await cache._callback(serializer.dumps, value)
    decoded = await cache._callback(serializer.loads, encoded)
    assert decoded.id == value["id"]
    assert decoded.created == value["created"]
    if codec == "MsgspecCodec":
        # aiodrf-async-cache 0.2 stores integers as Redis integers.
        assert serializer.supports_integer_operations
    else:
        with pytest.raises(NotImplementedError, match="counter"):
            await cache.aincr("unsupported")


@pytest.mark.parametrize("codec_name", ["MsgspecCodec", "PydanticCodec"])
@pytest.mark.parametrize(
    "value", [None, True, False, 0, -12, 3.5, "text", [1, None], {"key": [1, True]}]
)
def test_cache_codec_value_roundtrips(codec_name, value):
    cache_codecs = _codecs()

    codec = getattr(cache_codecs, codec_name)()
    assert codec.loads(codec.dumps(value)) == value


@pytest.mark.parametrize(
    ("annotation", "value"),
    [
        (float | None, float("nan")),
        (float, float("inf")),
        (list[float], [1.0, float("-inf")]),
        (str | float, "NaN"),
        (bytes, b"\xff\x00"),
    ],
)
def test_the_pydantic_codec_keeps_what_its_type_accepts(annotation, value):
    import math

    from fastdrf.codecs import PydanticCodec

    codec = PydanticCodec(annotation)
    loaded = codec.loads(codec.dumps(value))
    if isinstance(value, float) and math.isnan(value):
        assert math.isnan(loaded)
    else:
        assert loaded == value
        assert type(loaded) is type(value)
