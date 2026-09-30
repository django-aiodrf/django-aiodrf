"""
adrf's ``mixins``: aiodrf's actions under adrf's names as well.

Each adrf name calls aiodrf's implementation of the mixin directly, so an
override that calls ``super().alist()`` gets aiodrf's list, not itself. The
views and viewsets they are mixed into map adrf's names to aiodrf's.
"""

from typing import Any

from django.http import HttpResponseBase
from rest_framework.request import Request

from aiodrf import aio
from aiodrf import mixins as _mixins

__all__ = [
    "CreateModelMixin",
    "DestroyModelMixin",
    "ListModelMixin",
    "RetrieveModelMixin",
    "UpdateModelMixin",
    "get_data",
]


async def get_data(serializer: Any) -> Any:
    """adrf's: the serializer's data, awaited."""
    return await aio.data(serializer)


class CreateModelMixin(_mixins.CreateModelMixin):
    async def acreate(
        self, request: Request, *args: Any, **kwargs: Any
    ) -> HttpResponseBase:
        return await _mixins.CreateModelMixin.create(self, request, *args, **kwargs)

    async def perform_acreate(self, serializer: Any) -> None:
        await _mixins.CreateModelMixin.aperform_create(self, serializer)


class ListModelMixin(_mixins.ListModelMixin):
    async def alist(
        self, request: Request, *args: Any, **kwargs: Any
    ) -> HttpResponseBase:
        return await _mixins.ListModelMixin.list(self, request, *args, **kwargs)


class RetrieveModelMixin(_mixins.RetrieveModelMixin):
    async def aretrieve(
        self, request: Request, *args: Any, **kwargs: Any
    ) -> HttpResponseBase:
        return await _mixins.RetrieveModelMixin.retrieve(self, request, *args, **kwargs)


class UpdateModelMixin(_mixins.UpdateModelMixin):
    async def aupdate(
        self, request: Request, *args: Any, **kwargs: Any
    ) -> HttpResponseBase:
        return await _mixins.UpdateModelMixin.update(self, request, *args, **kwargs)

    async def partial_aupdate(
        self, request: Request, *args: Any, **kwargs: Any
    ) -> HttpResponseBase:
        kwargs["partial"] = True
        return await self.aupdate(request, *args, **kwargs)

    async def perform_aupdate(self, serializer: Any) -> None:
        await _mixins.UpdateModelMixin.aperform_update(self, serializer)


class DestroyModelMixin(_mixins.DestroyModelMixin):
    async def adestroy(
        self, request: Request, *args: Any, **kwargs: Any
    ) -> HttpResponseBase:
        return await _mixins.DestroyModelMixin.destroy(self, request, *args, **kwargs)

    async def perform_adestroy(self, instance: Any) -> None:
        await _mixins.DestroyModelMixin.aperform_destroy(self, instance)
