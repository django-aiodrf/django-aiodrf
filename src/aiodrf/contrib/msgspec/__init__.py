"""
msgspec integration.

* :class:`MsgspecSerializer`: a DRF serializer validated and rendered by a
  msgspec ``Struct``, documented by drf-spectacular from the same class.
* :class:`MsgspecJSONRenderer` / :class:`MsgspecJSONParser`: drop-in JSON
  renderer and parser.
* ``AIODRF["SERIALIZER_BACKEND"] = "msgspec"``: compile existing DRF
  serializers to Structs for representation (see :mod:`.compiler`).
"""

from aiodrf.contrib.msgspec.parsers import MsgspecJSONParser
from aiodrf.contrib.msgspec.renderers import MsgspecJSONRenderer
from aiodrf.contrib.msgspec.serializers import MsgspecSerializer, serializer_for

__all__ = [
    "MsgspecJSONParser",
    "MsgspecJSONRenderer",
    "MsgspecSerializer",
    "serializer_for",
]
