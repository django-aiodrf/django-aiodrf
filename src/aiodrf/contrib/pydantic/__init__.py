"""
pydantic integration.

* :class:`PydanticSerializer`: a DRF serializer validated and rendered by a
  pydantic model, documented by drf-spectacular from the same class.
* ``fastdrf.pydantic.parsers.PydanticJSONParser`` and
  ``fastdrf.pydantic.renderers.PydanticJSONRenderer``: opt-in JSON transport,
  configured through DRF independently of serializer selection.
* ``FASTDRF["SERIALIZER_BACKEND"] = "pydantic"``: compile existing DRF
  serializers to models for representation (``fastdrf.pydantic.compiler``).
"""

from aiodrf.contrib.pydantic.serializers import PydanticSerializer, serializer_for

__all__ = ["PydanticSerializer", "serializer_for"]
