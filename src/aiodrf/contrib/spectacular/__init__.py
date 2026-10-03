"""
drf-spectacular integration, loaded automatically by ``aiodrf.apps`` when
drf-spectacular is installed.

aiodrf views keep DRF's action names and subclass DRF's generic views, so
spectacular documents them without help. The extensions here cover aiodrf's
own authentication class and streaming responses; django-fastdrf's
``fastdrf.spectacular``, which ``aiodrf.apps`` loads with them, documents the
msgspec/pydantic schema serializers.
``AutoSchema`` (opt-in) documents query serializers as query parameters.
"""

from .schemas import AutoSchema
from .streaming import StreamSchema

__all__ = ["AutoSchema", "StreamSchema"]
