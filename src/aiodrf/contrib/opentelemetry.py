"""
OpenTelemetry spans for the phases of an aiodrf request (opt-in).

``TracingMixin`` wraps a view's authentication, permission and throttle
checks, its handler and its finalization in spans, as children of whatever
span is current (the server span of Django's instrumentation, usually)::

    class ArticleViewSet(TracingMixin, viewsets.ModelViewSet):
        ...

It uses the OpenTelemetry API only. The application owns the SDK: the
tracer provider, sampling and exporters are configured by the project;
until it sets a tracer provider, the mixin opens no spans at all. With a
provider whose sampler drops the trace, span contexts are still entered but
their spans are not recorded. Attributes are the view
class and the viewset action, never request data. DRF's expected answers
(401, 403, 404, 429, validation errors) leave a span's status unset and name
the exception in ``aiodrf.exception``; any other exception is recorded and
sets the status to error. A cancelled phase ends with ``aiodrf.cancelled``.

Validation, representation and saving happen inside the handler span;
rendering happens after the view returns, in Django's handler. Tracing them
separately would change how aiodrf dispatches the serializer, so they are
not split out.
"""

import asyncio
import contextlib
from collections.abc import Generator
from typing import Any

from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponseBase
from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode
from rest_framework.exceptions import APIException

from aiodrf.request import Request
from aiodrf.utils import _transparent

__all__ = ["TracingMixin"]

# A proxy: it follows the provider the application sets, whenever it does.
_tracer = trace.get_tracer(__name__)

# Answers DRF turns into responses: part of the request, not failures.
_EXPECTED = (APIException, Http404, PermissionDenied)


def _phase(view: Any, name: str) -> contextlib.AbstractContextManager[Any]:
    # Until the application sets a tracer provider, the API's proxy records
    # nothing; skip even the no-op span and its context switch.
    if isinstance(trace.get_tracer_provider(), trace.ProxyTracerProvider):
        return contextlib.nullcontext()
    return _span(view, name)


@contextlib.contextmanager
def _span(view: Any, name: str) -> Generator[Span, None, None]:
    attributes = {"aiodrf.view": f"{type(view).__module__}.{type(view).__qualname__}"}
    action = getattr(view, "action", None)
    if isinstance(action, str):
        attributes["aiodrf.action"] = action
    with _tracer.start_as_current_span(
        name,
        attributes=attributes,
        record_exception=False,
        set_status_on_exception=False,
    ) as span:
        try:
            yield span
        except _EXPECTED as exc:
            span.set_attribute("aiodrf.exception", type(exc).__qualname__)
            raise
        except asyncio.CancelledError:
            span.set_attribute("aiodrf.cancelled", True)
            raise
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, type(exc).__qualname__))
            raise


@_transparent
class TracingMixin:
    """
    Spans for authentication, permissions, throttles, handler and finalization.

    Each phase is a sync/async pair, and a view may implement either member
    (``check_permissions`` written for DRF, ``acheck_permissions`` for
    aiodrf). The mixin wraps both with ``super()`` and is transparent: the
    pair resolves as it would without the mixin, and the member that runs
    runs in a span.
    """

    def perform_authentication(self, request: Request) -> None:
        with _phase(self, "aiodrf.authenticate"):
            super().perform_authentication(request)  # type: ignore[misc]

    async def aperform_authentication(self, request: Request) -> None:
        with _phase(self, "aiodrf.authenticate"):
            await super().aperform_authentication(request)  # type: ignore[misc]

    def check_permissions(self, request: Request) -> None:
        with _phase(self, "aiodrf.check_permissions"):
            super().check_permissions(request)  # type: ignore[misc]

    async def acheck_permissions(self, request: Request) -> None:
        with _phase(self, "aiodrf.check_permissions"):
            await super().acheck_permissions(request)  # type: ignore[misc]

    def check_throttles(self, request: Request) -> None:
        with _phase(self, "aiodrf.check_throttles"):
            super().check_throttles(request)  # type: ignore[misc]

    async def acheck_throttles(self, request: Request) -> None:
        with _phase(self, "aiodrf.check_throttles"):
            await super().acheck_throttles(request)  # type: ignore[misc]

    async def acall_handler(
        self, handler: Any, request: Request, *args: Any, **kwargs: Any
    ) -> Any:
        with _phase(self, "aiodrf.handler"):
            return await super().acall_handler(handler, request, *args, **kwargs)  # type: ignore[misc]

    def finalize_response(
        self, request: Request, response: HttpResponseBase, *args: Any, **kwargs: Any
    ) -> HttpResponseBase:
        with _phase(self, "aiodrf.finalize"):
            return super().finalize_response(request, response, *args, **kwargs)  # type: ignore[misc]

    async def afinalize_response(
        self, request: Request, response: HttpResponseBase, *args: Any, **kwargs: Any
    ) -> HttpResponseBase:
        with _phase(self, "aiodrf.finalize"):
            return await super().afinalize_response(request, response, *args, **kwargs)  # type: ignore[misc]
