"""adrf's ``viewsets``: aiodrf's viewsets with adrf's action names."""

from aiodrf import viewsets
from aiodrf.contrib.adrf_compat._views import AdrfGenericMixin, AdrfViewMixin
from aiodrf.contrib.adrf_compat.adrf import mixins
from aiodrf.viewsets import ViewSetMixin

__all__ = [
    "GenericViewSet",
    "ModelViewSet",
    "ReadOnlyModelViewSet",
    "ViewSet",
    "ViewSetMixin",
]


class ViewSet(AdrfViewMixin, viewsets.ViewSet):
    pass


class GenericViewSet(AdrfGenericMixin, viewsets.GenericViewSet):
    pass


class ReadOnlyModelViewSet(
    mixins.RetrieveModelMixin,
    mixins.ListModelMixin,
    AdrfGenericMixin,
    viewsets.ReadOnlyModelViewSet,
):
    pass


class ModelViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    AdrfGenericMixin,
    viewsets.ModelViewSet,
):
    pass
