"""
pydantic integration.

* :class:`PydanticSerializer`: a DRF serializer validated and rendered by a
  pydantic model, documented by drf-spectacular from the same class.
* ``AIODRF["SERIALIZER_BACKEND"] = "pydantic"``: compile existing DRF
  serializers to models for representation (see :mod:`.compiler`).
"""

from aiodrf.contrib.pydantic.serializers import PydanticSerializer, serializer_for

__all__ = ["PydanticSerializer", "serializer_for"]
