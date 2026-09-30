"""Validation of DRF extension points that remain synchronous.

This is a contract check, not execution classification. Unknown synchronous
implementations still belong in a worker; async implementations need an
explicit dispatcher rather than an unawaited coroutine in upstream DRF code.
"""

from inspect import getattr_static
from typing import Any

from django.core.exceptions import ImproperlyConfigured

from aiodrf.utils import class_cache, is_async_callable

# Only non-paired view hooks. Handlers and paired methods may be async.
_SYNC_VIEW_HOOKS = (
    "initialize_request",
    "get_parser_context",
    "get_parsers",
    "get_authenticators",
    "get_content_negotiator",
    "get_renderers",
    "get_permissions",
    "get_throttles",
    "get_format_suffix",
    "perform_content_negotiation",
    "determine_version",
    "get_exception_handler",
    "get_exception_handler_context",
    "get_renderer_context",
    "get_success_headers",
    "get_query_serializer",
    "get_query_serializer_class",
    # Called to raise: an unawaited coroutine would let a denied request through.
    "permission_denied",
    "throttled",
)


def require_sync_hooks(obj: Any, *names: str) -> None:
    """Inspect without constructing objects or evaluating descriptors."""
    # Parsers and renderers are checked for every request. What their class
    # defines is checked once per class; only a hook set on the instance
    # itself has to be looked at every time.
    if isinstance(obj, type):
        _require_sync_class_hooks(obj, names)
        return
    instance = getattr(obj, "__dict__", None)
    if instance and not instance.keys().isdisjoint(names):
        _check_sync_hooks(obj, names)
    else:
        _require_sync_class_hooks(type(obj), names)


@class_cache
def _require_sync_class_hooks(cls: type, names: tuple[str, ...]) -> None:
    _check_sync_hooks(cls, names)


def _check_sync_hooks(obj: Any, names: tuple[str, ...]) -> None:
    cls = obj if isinstance(obj, type) else type(obj)
    for name in names:
        method = getattr_static(obj, name, None)
        if isinstance(method, (classmethod, staticmethod)):
            method = method.__func__
        elif not callable(method) and hasattr(type(method), "__get__"):
            # A property's value is known only once it runs.
            raise ImproperlyConfigured(
                f"{cls.__module__}.{cls.__qualname__}.{name} must be a method: "
                "aiodrf cannot tell whether what a descriptor returns is synchronous."
            )
        _check_sync_value(cls, name, method)


def _check_sync_value(cls: type, name: str, method: object) -> None:
    if callable(method) and is_async_callable(method):
        raise ImproperlyConfigured(
            f"{cls.__module__}.{cls.__qualname__}.{name} must be synchronous. "
            "This DRF hook has no async dispatcher; keep synchronous I/O in "
            "the hook's worker boundary or move async I/O to a supported async hook. "
            "See the aiodrf extension-hooks guide."
        )


def check_view_hooks(cls: type, initkwargs: dict[str, Any]) -> None:
    """Validate static view configuration at URL construction, not per request."""
    require_sync_hooks(cls, *_SYNC_VIEW_HOOKS)
    sync_hooks = set(_SYNC_VIEW_HOOKS)
    for name in ("get_serializer", "get_serializer_class", "get_serializer_context"):
        if not hasattr(cls, f"a{name}"):
            require_sync_hooks(cls, name)
            sync_hooks.add(name)
    # ``as_view()`` arguments become attributes of each instance.
    for name, value in initkwargs.items():
        if name in sync_hooks:
            _check_sync_value(cls, name, value)
    for name, hooks in (
        ("parser_classes", ("parse",)),
        ("renderer_classes", ("render",)),
    ):
        # None leaves the choice to the instance (DRF's ``SchemaView``); the
        # components are then checked when they are used.
        for component in initkwargs.get(name, getattr(cls, name)) or ():
            require_sync_hooks(component, *hooks)
    for single, methods in (
        ("content_negotiation_class", ("select_parser", "select_renderer")),
        ("versioning_class", ("determine_version",)),
        (
            "metadata_class",
            (
                "determine_metadata",
                "determine_actions",
                "get_serializer_info",
                "get_field_info",
            ),
        ),
    ):
        component = initkwargs.get(single, getattr(cls, single))
        if component is not None:
            require_sync_hooks(component, *methods)
