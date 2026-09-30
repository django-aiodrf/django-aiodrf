"""DRF router exports for aiodrf viewsets."""

# aiodrf keeps DRF's action names, so DRF's routers work unchanged. This
# module only exists so that ``aiodrf.routers`` mirrors ``rest_framework.routers``.
from rest_framework.routers import (
    APIRootView,
    BaseRouter,
    DefaultRouter,
    DynamicRoute,
    Route,
    SimpleRouter,
)

__all__ = [
    "APIRootView",
    "BaseRouter",
    "DefaultRouter",
    "DynamicRoute",
    "Route",
    "SimpleRouter",
]
