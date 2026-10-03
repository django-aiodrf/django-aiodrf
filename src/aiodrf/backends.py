"""
Which library defines a view's serializer, and whether the project allows it
(``FASTDRF["ALLOWED_SERIALIZER_BACKENDS"]``).

A view's static serializer is resolved when its URL is built (``as_view``):
a bare msgspec ``Struct`` or pydantic model is wrapped in its serializer
then, and a kind the setting does not allow is refused then, not on the
first request. A serializer a view chooses per request
(``get_serializer_class`` overridden) is checked when it is built.
"""

from fastdrf.typed import _resolve
from rest_framework.serializers import BaseSerializer

__all__ = ["compile_serializer"]


def compile_serializer(serializer_class: object, owner: type) -> type[BaseSerializer]:
    """
    The serializer class for what a view names as its serializer: checked
    against the setting, and a bare schema class wrapped (once per class) in
    aiodrf's schema serializer.
    """
    return _resolve(serializer_class, owner, _adapt)


def _adapt(schema: type) -> type:
    # Only bare schema classes get here, so the core does not import
    # ``aiodrf.contrib`` unless a project uses it.
    from aiodrf.contrib.typed import adapt

    return adapt(schema)
