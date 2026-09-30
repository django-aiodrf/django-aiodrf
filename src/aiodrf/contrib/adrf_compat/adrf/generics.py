"""adrf's ``generics``: aiodrf's generic views with adrf's members."""

from typing import Any

from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import aget_object_or_404 as _aget_object_or_404

from aiodrf import generics
from aiodrf.contrib.adrf_compat._views import AdrfGenericMixin
from aiodrf.contrib.adrf_compat.adrf import mixins

__all__ = [
    "CreateAPIView",
    "DestroyAPIView",
    "GenericAPIView",
    "ListAPIView",
    "ListCreateAPIView",
    "RetrieveAPIView",
    "RetrieveDestroyAPIView",
    "RetrieveUpdateAPIView",
    "RetrieveUpdateDestroyAPIView",
    "UpdateAPIView",
    "aget_object_or_404",
]


async def aget_object_or_404(
    queryset: Any, *filter_args: Any, **filter_kwargs: Any
) -> Any:
    """Django's, and a 404 for a lookup value of the wrong type, as DRF's."""
    try:
        return await _aget_object_or_404(queryset, *filter_args, **filter_kwargs)
    except (TypeError, ValueError, ValidationError):
        raise Http404 from None


class GenericAPIView(AdrfGenericMixin, generics.GenericAPIView):
    pass


class CreateAPIView(mixins.CreateModelMixin, GenericAPIView, generics.CreateAPIView):
    pass


class ListAPIView(mixins.ListModelMixin, GenericAPIView, generics.ListAPIView):
    pass


class RetrieveAPIView(
    mixins.RetrieveModelMixin, GenericAPIView, generics.RetrieveAPIView
):
    pass


class DestroyAPIView(mixins.DestroyModelMixin, GenericAPIView, generics.DestroyAPIView):
    pass


class UpdateAPIView(mixins.UpdateModelMixin, GenericAPIView, generics.UpdateAPIView):
    pass


class ListCreateAPIView(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    GenericAPIView,
    generics.ListCreateAPIView,
):
    pass


class RetrieveUpdateAPIView(
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    GenericAPIView,
    generics.RetrieveUpdateAPIView,
):
    pass


class RetrieveDestroyAPIView(
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    GenericAPIView,
    generics.RetrieveDestroyAPIView,
):
    pass


class RetrieveUpdateDestroyAPIView(
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    GenericAPIView,
    generics.RetrieveUpdateDestroyAPIView,
):
    pass
