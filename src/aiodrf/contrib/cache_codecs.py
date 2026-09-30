"""Optional value codecs for the native Redis and Valkey cache backends.

The cache awaits async callbacks and offloads these synchronous codecs by
default. Their wire formats are not Django's pickle-based page-cache format.
"""

from collections.abc import Callable
from typing import Any

__all__ = ["MsgspecCodec", "PydanticCodec"]


class MsgspecCodec:
    """MessagePack values, optionally decoded into a msgspec type.

    ``enc_hook`` and ``dec_hook`` retain msgspec's synchronous callback contract.
    They execute with the codec in the cache's configured callback context.
    """

    def __init__(
        self,
        type: Any = Any,
        *,
        enc_hook: Callable[[Any], Any] | None = None,
        dec_hook: Callable[[type, Any], Any] | None = None,
    ) -> None:
        import msgspec

        self.encoder = msgspec.msgpack.Encoder(enc_hook=enc_hook)
        self.decoder = msgspec.msgpack.Decoder(type, dec_hook=dec_hook)

    def dumps(self, value: Any) -> bytes:
        return self.encoder.encode(value)

    def loads(self, value: bytes) -> Any:
        return self.decoder.decode(value)


# What a cached value needs of Pydantic's JSON: non-finite floats written as
# JSON's usual extension (by default they become ``null``) and bytes as base64
# (by default they must be UTF-8), so that what the type accepts comes back.
_ROUND_TRIP = {
    "ser_json_inf_nan": "constants",
    "ser_json_bytes": "base64",
    "val_json_bytes": "base64",
}


class PydanticCodec:
    """Validate and serialize cached values with one reusable TypeAdapter.

    Pass a model, a type expression or an existing TypeAdapter. Validation and
    serialization follow Pydantic's contract, not DRF's field coercion rules.
    A type expression gets a configuration under which every value it accepts
    round-trips (non-finite floats, arbitrary bytes). A model, dataclass or
    TypedDict carries its own configuration, and a given TypeAdapter is used
    as it is: set ``ser_json_inf_nan`` and the bytes modes there if needed.
    """

    def __init__(self, type: Any = Any) -> None:
        from pydantic import ConfigDict, PydanticUserError, TypeAdapter

        if isinstance(type, TypeAdapter):
            self.adapter = type
            return
        try:
            self.adapter = TypeAdapter(type, config=ConfigDict(**_ROUND_TRIP))  # type: ignore[typeddict-item]
        except PydanticUserError as exc:
            if exc.code != "type-adapter-config-unused":
                raise
            self.adapter = TypeAdapter(type)

    def dumps(self, value: Any) -> bytes:
        return self.adapter.dump_json(self.adapter.validate_python(value))

    def loads(self, value: bytes) -> Any:
        return self.adapter.validate_json(value)
