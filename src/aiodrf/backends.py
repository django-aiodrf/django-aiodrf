"""
Which library defines a view's serializer, and whether the project allows it
(``AIODRF["ALLOWED_SERIALIZER_BACKENDS"]``).

A view's static serializer is resolved when its URL is built (``as_view``):
a bare msgspec ``Struct`` or pydantic model is wrapped in its serializer
then, and a kind the setting does not allow is refused then, not on the
first request. A serializer a view chooses per request
(``get_serializer_class`` overridden) is checked when it is built.
"""

from django.core.exceptions import ImproperlyConfigured
from rest_framework.serializers import BaseSerializer

from aiodrf.settings import aiodrf_settings
from aiodrf.utils import class_cache

__all__ = ["compile_serializer", "serializer_kind"]

_SCHEMA_BASES = {
    ("msgspec", "Struct"): "msgspec",
    ("pydantic", "BaseModel"): "pydantic",
}


@class_cache
def serializer_kind(serializer_class: type) -> str:
    """``"drf"``, ``"msgspec"`` or ``"pydantic"``, without importing either library."""
    if issubclass(serializer_class, BaseSerializer):
        # Schema serializers name their library; everything else is DRF's.
        return getattr(serializer_class, "schema_library", "drf")
    for klass in serializer_class.__mro__:
        if klass.__module__.startswith("pydantic.v1."):
            raise ImproperlyConfigured(
                f"{_name(serializer_class)} is a pydantic.v1 model; aiodrf's pydantic "
                "serializers take pydantic 2's BaseModel."
            )
        kind = _SCHEMA_BASES.get((klass.__module__.split(".", 1)[0], klass.__name__))
        if kind is not None:
            return kind
    raise ImproperlyConfigured(
        f"{_name(serializer_class)} is neither a serializer nor a msgspec Struct / pydantic model."
    )


def require_allowed(serializer_class: type, owner: type) -> None:
    kind = serializer_kind(serializer_class)
    allowed = aiodrf_settings.ALLOWED_SERIALIZER_BACKENDS
    if kind not in allowed:
        raise ImproperlyConfigured(
            f"{_name(owner)} uses {_name(serializer_class)}, a {kind} serializer; "
            f"AIODRF['ALLOWED_SERIALIZER_BACKENDS'] allows {', '.join(allowed)}."
        )


def compile_serializer(serializer_class: object, owner: type) -> type[BaseSerializer]:
    """
    The serializer class for what a view names as its serializer: checked
    against the setting, and a bare schema class wrapped (once per class).
    """
    if not isinstance(serializer_class, type):
        raise ImproperlyConfigured(
            f"{_name(owner)}.serializer_class is {serializer_class!r}, not a class."
        )
    require_allowed(serializer_class, owner)
    if issubclass(serializer_class, BaseSerializer):
        return serializer_class
    # Only bare schema classes get here, so the core does not import
    # ``aiodrf.contrib`` unless a project uses it.
    from aiodrf.contrib.typed import adapt

    return adapt(serializer_class)


def _name(cls: type) -> str:
    return f"{cls.__module__}.{cls.__qualname__}"
