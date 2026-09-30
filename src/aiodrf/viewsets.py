"""
Async viewsets. Action names are DRF's, so DRF's routers work unchanged.

Class documentation in this module is written as comments: drf-spectacular
publishes the docstrings of view classes it does not recognise as DRF's own
as API descriptions.
"""

from typing import Any

from asgiref.sync import markcoroutinefunction
from django.db.models import Model
from django.utils.decorators import classonlymethod
from rest_framework import viewsets

from aiodrf import mixins
from aiodrf.generics import GenericAPIView
from aiodrf.utils import bridge_base
from aiodrf.views import APIView

__all__ = [
    "GenericViewSet",
    "ModelViewSet",
    "ReadOnlyModelViewSet",
    "ViewSet",
    "ViewSetMixin",
]

for _base in (
    viewsets.ViewSetMixin,
    viewsets.ViewSet,
    viewsets.GenericViewSet,
    viewsets.ReadOnlyModelViewSet,
    viewsets.ModelViewSet,
):
    bridge_base(_base)


@bridge_base
class ViewSetMixin(viewsets.ViewSetMixin):
    # DRF's ``ViewSetMixin`` for async views.
    #
    # Action names are unchanged (``list``, ``create``, ``retrieve``, ...), so
    # DRF's routers and drf-spectacular work as they do for DRF viewsets.
    # Actions may be ``async def`` or plain functions.

    @classonlymethod
    def as_view(cls, actions: Any = None, **initkwargs: Any) -> Any:
        # DRF's view function returns ``self.dispatch(...)``, which is a
        # coroutine here; marking the function makes Django await it.
        return markcoroutinefunction(super().as_view(actions, **initkwargs))

    def initialize_request(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """
        Set the `.action` attribute on the view, depending on the request method.
        """
        # Defined here, not inherited from DRF, so that ``super()`` reaches
        # aiodrf's ``APIView.initialize_request`` in the viewset MRO.
        request = super().initialize_request(request, *args, **kwargs)
        method = request.method.lower()
        if method == "options":
            # This is a special case as we always provide handling for the
            # options method in the base `View` class.
            # Unlike the other explicitly defined actions, 'metadata' is implicit.
            self.action = "metadata"
        else:
            # None for a method the viewset does not map, as in DRF (whose
            # stubs type ``action`` as ``str``).
            self.action = self.action_map.get(method)  # type: ignore[assignment]
        return request


@bridge_base
class ViewSet(ViewSetMixin, APIView, viewsets.ViewSet):
    # The base ViewSet class does not provide any actions by default.
    pass


@bridge_base
class GenericViewSet[ModelT: Model](
    ViewSetMixin, GenericAPIView[ModelT], viewsets.GenericViewSet
):
    # The GenericViewSet class does not provide any actions by default,
    # but does include the base set of generic view behavior, such as
    # the `get_object` and `get_queryset` methods.
    pass


@bridge_base
class ReadOnlyModelViewSet[ModelT: Model](
    mixins.RetrieveModelMixin,
    mixins.ListModelMixin,
    GenericViewSet[ModelT],
    viewsets.ReadOnlyModelViewSet,
):
    # A viewset that provides default `list()` and `retrieve()` actions.
    pass


@bridge_base
class ModelViewSet[ModelT: Model](
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    mixins.ListModelMixin,
    GenericViewSet[ModelT],
    viewsets.ModelViewSet,
):
    # A viewset that provides default `create()`, `retrieve()`, `update()`,
    # `partial_update()`, `destroy()` and `list()` actions.
    pass
