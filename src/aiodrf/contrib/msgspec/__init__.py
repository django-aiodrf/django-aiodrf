"""
msgspec integration.

* :class:`MsgspecSerializer`: a DRF serializer validated and rendered by a
  msgspec ``Struct``, documented by drf-spectacular from the same class.

django-fastdrf provides the rest: the JSON renderer and parser
(``fastdrf.msgspec.renderers.MsgspecJSONRenderer``,
``fastdrf.msgspec.parsers.MsgspecJSONParser``) and the compiled output of
existing DRF serializers (``FASTDRF["SERIALIZER_BACKEND"] = "msgspec"``).
"""

from aiodrf.contrib.msgspec.serializers import MsgspecSerializer, serializer_for

__all__ = ["MsgspecSerializer", "serializer_for"]
