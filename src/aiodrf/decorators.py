"""Function-based API views and DRF policy decorators."""

import types
from collections.abc import Callable, Sequence
from typing import Any

from rest_framework.decorators import (
    action,
    authentication_classes,
    parser_classes,
    permission_classes,
    renderer_classes,
    schema,
    throttle_classes,
)

try:  # DRF 3.17+
    from rest_framework.decorators import (
        content_negotiation_class,
        metadata_class,
        versioning_class,
    )
except ImportError:  # pragma: no cover - depends on the DRF version
    _DRF_317_DECORATORS = False
else:
    _DRF_317_DECORATORS = True

from aiodrf.utils import awaits_inline
from aiodrf.views import APIView

__all__ = [
    "action",
    "api_view",
    "authentication_classes",
    "parser_classes",
    "permission_classes",
    "renderer_classes",
    "schema",
    "throttle_classes",
]
if _DRF_317_DECORATORS:
    __all__ += ["content_negotiation_class", "metadata_class", "versioning_class"]


def api_view(
    http_method_names: Sequence[str] | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """
    Decorator that converts a function-based view into an aiodrf APIView
    subclass. ``async def`` functions are awaited on the event loop; plain
    functions run in a thread, as does a synchronous decorator around an
    ``async def`` function, whose coroutine is then awaited on the event loop.
    """
    http_method_names = ["GET"] if (http_method_names is None) else http_method_names

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        # drf-spectacular recognises function-based views by this name.
        WrappedAPIView: Any = type(
            "WrappedAPIView", (APIView,), {"__doc__": func.__doc__}
        )

        # api_view applied without (method_names)
        assert not isinstance(http_method_names, types.FunctionType), (  # noqa: S101 -- as in DRF
            "@api_view missing list of allowed HTTP methods"
        )

        # api_view applied with eg. string instead of list of strings
        assert isinstance(http_method_names, (list, tuple)), (  # noqa: S101 -- as in DRF
            "@api_view expected a list of strings, received %s"
            % type(http_method_names).__name__
        )

        allowed_methods = set(http_method_names) | {"options"}
        WrappedAPIView.http_method_names = [
            method.lower() for method in allowed_methods
        ]

        async def awaited(self: APIView, *args: Any, **kwargs: Any) -> Any:
            return await func(*args, **kwargs)

        def called(self: APIView, *args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)

        handler = awaited if awaits_inline(func) else called
        for method in http_method_names:
            setattr(WrappedAPIView, method.lower(), handler)

        WrappedAPIView.__name__ = func.__name__
        WrappedAPIView.__module__ = func.__module__

        for attr in (
            "renderer_classes",
            "parser_classes",
            "authentication_classes",
            "throttle_classes",
            "permission_classes",
            "content_negotiation_class",
            "metadata_class",
            "versioning_class",
            "schema",
        ):
            setattr(WrappedAPIView, attr, getattr(func, attr, getattr(APIView, attr)))
        WrappedAPIView.throttle_scope = getattr(func, "throttle_scope", None)

        return WrappedAPIView.as_view()

    return decorator
