"""
Async operations on DRF serializers.

The functions in this module accept *any* DRF serializer: plain
``rest_framework.serializers`` classes, aiodrf serializers and third-party
subclasses. They choose the cheapest safe way to run DRF's own code:

* a serializer without async members is validated, represented or saved by
  DRF's own code in **one** thread hop, fields being built and classified
  inside that hop (a ``get_fields()`` written for DRF may query);
* validation that is known to do no I/O runs inline on the event loop;
* ``async def`` hooks (``validate_<field>``, ``validate``, validators,
  ``SerializerMethodField`` methods, model properties) are awaited, in DRF's
  order, with the synchronous work between them in as few hops as that
  order allows.

Nothing is tried on the event loop first and repeated in a thread: code runs
once, where the classification puts it.

``is_valid``, ``data`` and ``save`` are the public entry points. They respect
user overrides of either member of each sync/async pair (``is_valid`` /
``ais_valid``, ``create`` / ``acreate``, ...).

The implementation is split into private modules: ``_classify``,
``_validate``, ``_represent``, ``_save`` and ``_common``. Only the names in
``__all__`` are public.
"""

from rest_framework import fields as _fields
from rest_framework import serializers as _serializers

from aiodrf.aio._common import NEEDS_AWAIT
from aiodrf.aio._represent import (
    data,
    default_to_representation,
    to_representation,
    try_data,
)
from aiodrf.aio._save import default_save, save, try_save
from aiodrf.aio._validate import (
    default_is_valid,
    default_run_validation,
    is_valid,
    run_validation,
    try_is_valid,
)
from aiodrf.utils import bridge_base as _bridge_base

__all__ = [
    "NEEDS_AWAIT",
    "data",
    "default_is_valid",
    "default_run_validation",
    "default_save",
    "default_to_representation",
    "is_valid",
    "run_validation",
    "save",
    "to_representation",
    "try_data",
    "try_is_valid",
    "try_save",
]

for _base in (
    _fields.Field,
    _serializers.BaseSerializer,
    _serializers.Serializer,
    _serializers.ListSerializer,
    _serializers.ModelSerializer,
    _serializers.HyperlinkedModelSerializer,
):
    _bridge_base(_base)
del _base, _bridge_base, _fields, _serializers
