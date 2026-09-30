"""
adrf's ``serializers``: aiodrf's serializer classes with adrf's fields.

``await serializer.adata`` (adrf's property) and ``await serializer.adata()``
both work; ``acreate``, ``aupdate``, ``asave`` and ``ato_representation``
keep their names. adrf's ``ModelSerializer`` implements ``acreate`` and
``aupdate``, so an override may extend them with ``super()``.
"""

from typing import Any

from rest_framework import serializers as drf_serializers

from aiodrf import serializers as _serializers
from aiodrf.contrib.adrf_compat.adrf import fields
from aiodrf.contrib.adrf_compat.adrf.fields import *  # noqa: F403 -- adrf's namespace
from aiodrf.serializers import BaseSerializer, ListSerializer, Serializer
from aiodrf.utils import bridge_base, run_sync

__all__ = ["BaseSerializer", "ListSerializer", "ModelSerializer", "Serializer"]
__all__ += fields.__all__


@bridge_base
class ModelSerializer(_serializers.ModelSerializer):
    # A bridge base: these defaults are not an override, and a save without
    # one takes aiodrf's own path (``ATOMIC_SAVE``, one thread hop). DRF's
    # write is called by name, as adrf's does: ``self.create`` is aiodrf's
    # bridge, which would come back to an ``acreate`` override.

    async def acreate(self, validated_data: Any) -> Any:
        create = drf_serializers.ModelSerializer.create
        return await run_sync(create)(self, validated_data)

    async def aupdate(self, instance: Any, validated_data: Any) -> Any:
        update = drf_serializers.ModelSerializer.update
        return await run_sync(update)(self, instance, validated_data)
