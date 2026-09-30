"""
Run DRF's own test suite with aiodrf's view layer (``nox -s drf_parity``).

A pytest plugin, loaded with ``-p aiodrf_drf_parity`` (this directory on
``PYTHONPATH``) from a checkout of DRF's sources of the installed version.
Before DRF's test modules are imported, the view classes they build on are
replaced with aiodrf's: ``APIView``, the generic views and mixins, the
viewsets and ``api_view``. Serializers, fields, permissions and the rest stay
DRF's: aiodrf's claim is that DRF code runs unchanged in its views.

DRF's tests call views synchronously, as Django's WSGI handler does; aiodrf's
views are coroutine functions, so each ``as_view()`` result is wrapped in
``async_to_sync`` here, which is what Django does for an async view under
WSGI. ``AIODRF["ATOMIC_SAVE"]`` is off, so that DRF's query counts apply;
its default is documented as a difference. This rewiring exists only in this
harness.

Known differences, each with its reason, are in ``EXPECTED_DIFFERENCES``;
they are marked xfail (strict), so a difference that disappears is reported.
"""

import functools

import pytest
from asgiref.sync import async_to_sync
from django.utils.decorators import classonlymethod

_HANDLERS_ARE_COROUTINES = (
    "aiodrf's generic handlers (post, get, ...) are coroutine functions; "
    "a synchronous caller gets a coroutine, as with any async Django view"
)
_QUERY_METHOD = (
    "aiodrf dispatches HTTP QUERY (RFC 10008) until Django does (django#37232); "
    "DRF's test expects every http_method_names entry in http.HTTPMethod"
)
_ATOMIC_REQUESTS = (
    "Django refuses ATOMIC_REQUESTS for async views (aiodrf.W002), so aiodrf "
    "has no request-wide transaction to mark for rollback"
)

# DRF test id -> why aiodrf differs there, on purpose.
EXPECTED_DIFFERENCES: dict[str, str] = {
    **{
        f"tests/test_generics.py::ApiViewsTests::{name}": _HANDLERS_ARE_COROUTINES
        for name in (
            "test_create_api_view_post",
            "test_destroy_api_view_delete",
            "test_retrieve_destroy_api_view_delete",
            "test_retrieve_destroy_api_view_get",
            "test_retrieve_update_api_view_get",
            "test_retrieve_update_api_view_patch",
            "test_retrieve_update_api_view_put",
            "test_update_api_view_partial_update",
        )
    },
    "tests/test_viewsets.py::InitializeViewSetsTestCase::"
    "test_args_kwargs_request_action_map_on_self": (
        "a dispatch() override must return an awaitable in aiodrf; this one returns a Response"
    ),
    "tests/test_decorators.py::ActionDecoratorTestCase::test_method_mapping_http_method": (
        _QUERY_METHOD
    ),
    "tests/test_decorators.py::ActionDecoratorTestCase::test_method_mapping_http_methods": (
        _QUERY_METHOD
    ),
    "tests/test_atomic_requests.py::DBTransactionAPIExceptionTests::"
    "test_api_exception_rollback_transaction": _ATOMIC_REQUESTS,
    "tests/test_atomic_requests.py::MultiDBTransactionAPIExceptionTests::"
    "test_api_exception_rollback_transaction": _ATOMIC_REQUESTS,
}


def _synchronous(view):
    @functools.wraps(view)
    def run(request, *args, **kwargs):
        return async_to_sync(view)(request, *args, **kwargs)

    # functools.wraps copies the coroutine marker; this wrapper is synchronous.
    for marker in ("_is_coroutine_marker", "_is_coroutine"):
        run.__dict__.pop(marker, None)
    return run


class _Aliased(type):
    """
    Lets ``rest_framework.views.APIView`` stand for aiodrf's ``APIView``:
    DRF checks ``issubclass(cls, APIView)`` (breadcrumbs, schemas), and the
    replaced generic views derive from aiodrf's class, not from this one.
    """

    def __subclasscheck__(cls, subclass):
        target = cls.__dict__.get("_aliases")
        return super().__subclasscheck__(subclass) or (
            target is not None and issubclass(subclass, target)
        )

    def __instancecheck__(cls, instance):
        return cls.__subclasscheck__(type(instance))


def _harness(base, *, alias=False):
    from rest_framework.viewsets import ViewSetMixin

    from aiodrf.utils import bridge_base

    # The same decorator as DRF: a classmethod on APIView, a classonlymethod
    # on viewsets.
    decorator = classonlymethod if issubclass(base, ViewSetMixin) else classmethod

    def as_view(cls, *args, **initkwargs):
        return _synchronous(super(harness, cls).as_view(*args, **initkwargs))

    namespace = {"as_view": decorator(as_view), "__module__": base.__module__}
    if alias:
        namespace["_aliases"] = base
    metaclass = _Aliased if alias else type(base)
    harness = metaclass(base.__name__, (base,), namespace)
    harness.__qualname__ = base.__qualname__
    return bridge_base(harness)


@pytest.hookimpl(trylast=True)
def pytest_configure(config):
    from django.conf import settings
    from rest_framework import decorators, generics, mixins, views, viewsets

    from aiodrf import decorators as aio_decorators
    from aiodrf import generics as aio_generics
    from aiodrf import mixins as aio_mixins
    from aiodrf import viewsets as aio_viewsets
    from aiodrf.settings import aiodrf_settings
    from aiodrf.views import APIView

    settings.AIODRF = {**getattr(settings, "AIODRF", {}), "ATOMIC_SAVE": False}
    aiodrf_settings.reload()

    views.APIView = _harness(APIView, alias=True)
    for module, aio_module in ((generics, aio_generics), (viewsets, aio_viewsets)):
        for name, value in vars(aio_module).items():
            if (
                isinstance(value, type)
                and hasattr(module, name)
                and hasattr(value, "as_view")
            ):
                setattr(module, name, _harness(value))
    for name, value in vars(aio_mixins).items():
        if isinstance(value, type) and name.endswith("Mixin") and hasattr(mixins, name):
            setattr(mixins, name, value)

    def api_view(http_method_names=None):
        def decorator(func):
            return _synchronous(aio_decorators.api_view(http_method_names)(func))

        return decorator

    decorators.api_view = api_view


def pytest_collection_modifyitems(items):
    for item in items:
        reason = EXPECTED_DIFFERENCES.get(item.nodeid)
        if reason:
            item.add_marker(pytest.mark.xfail(reason=reason, strict=True))
