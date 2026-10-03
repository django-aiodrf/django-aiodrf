"""
Async-native ``APIView``.

``dispatch`` is a coroutine. The pre-handler hooks have async counterparts
(``ainitial``, ``aperform_authentication``, ``acheck_permissions``,
``acheck_throttles``, ``acheck_object_permissions``, ``ahandle_exception``)
that dispatch awaits, while the synchronous DRF methods stay available for
code that calls them synchronously: drf-spectacular, the browsable API and
``OPTIONS`` metadata.

Overriding only the synchronous member of a pair, as code written for DRF
does, keeps working: aiodrf detects the override and runs it in a thread.
"""

import copy
import datetime
import re
import threading
from collections.abc import Callable, Iterable, Iterator
from typing import Any

from asgiref.sync import async_to_sync
from django.http import HttpRequest, HttpResponseBase
from django.utils import timezone
from django.utils.cache import get_conditional_response
from django.utils.functional import classproperty
from django.utils.http import http_date, parse_header_parameters, quote_etag
from django.views.generic import View
from fastdrf.response import DataResponse
from fastdrf.views import _accept_header, _is_drf_negotiation, _query_param
from rest_framework import exceptions, permissions, serializers, status, views
from rest_framework.negotiation import DefaultContentNegotiation
from rest_framework.request import Request as DRFRequest
from rest_framework.views import (  # noqa: F401
    exception_handler,
    get_view_description,
    get_view_name,
)

from aiodrf import aio, policies
from aiodrf.aio._classify import is_declarative_class
from aiodrf.backends import compile_serializer
from aiodrf.compat import DJANGO_HAS_QUERY
from aiodrf.exceptions import PreconditionFailed
from aiodrf.hooks import check_view_hooks, require_sync_hooks
from aiodrf.policies import Mode
from aiodrf.request import Request
from aiodrf.response import (
    Response,
    aresolve_data_response,
    resolve_data_response,
    upgrade_response,
)
from aiodrf.utils import (
    Impl,
    async_safe,
    awaits_inline,
    bridge_base,
    bridges_to,
    call_pair,
    call_pair_sync,
    class_cache,
    depends_on_classification,
    is_framework_class,
    is_pure,
    is_pure_function,
    maybe_await,
    register_pure,
    resolve_pair,
    run_sync,
    run_sync_and_await,
    user_defines,
)

__all__ = ["APIView", "accept_query", "exception_handler"]

bridge_base(views.APIView)

# The hooks behind each step of the request lifecycle. DRF's implementations
# only build objects and read headers, so they run on the event loop; a
# project's override may do anything, and then the step runs in a thread.
_INITIALIZE_HOOKS = (
    "initialize_request",
    "get_parser_context",
    "get_parsers",
    "get_authenticators",
    "get_content_negotiator",
)
_NEGOTIATION_HOOKS = (
    "get_format_suffix",
    "perform_content_negotiation",
    "get_renderers",
    "get_content_negotiator",
    "determine_version",
)
# Overriding one of these replaces DRF's negotiation (``_perform_content_negotiation``).
_CONTENT_NEGOTIATION_HOOKS = (
    "perform_content_negotiation",
    "get_renderers",
    "get_content_negotiator",
)
# Content negotiation results (``APIView._perform_content_negotiation``). The
# key holds the client's ``Accept`` header, so the cache is bounded: the
# values a server sees are few (browsers, HTTP libraries), a longer header is
# not kept, and a full cache is emptied.
_NEGOTIATION_CACHE_SIZE = 1024
_MAX_KEPT_ACCEPT = 256
_negotiations: dict[tuple[Any, ...], tuple[int, str]] = {}
# Publication and eviction form one operation, including without the GIL.
_negotiation_lock = threading.Lock()


def _keep_negotiation(cache: Any, key: Any, value: Any) -> None:
    """
    Publish to a bounded negotiation cache. Eviction and publication form one
    operation, including without the GIL: threads that each found the cache
    below its bound cannot together pass it.
    """
    with _negotiation_lock:
        if len(cache) >= _NEGOTIATION_CACHE_SIZE:
            cache.clear()
        cache[key] = value


_DRF_NEGOTIATION = (
    DefaultContentNegotiation.select_renderer,
    DefaultContentNegotiation.filter_renderers,
    DefaultContentNegotiation.get_accept_list,
)


# Where a project's code runs between building DRF's request and negotiating.
_BEFORE_NEGOTIATION_HOOKS = (
    "dispatch",
    "initialize_request",
    "initial",
    "ainitial",
    "get_format_suffix",
)


@class_cache
def _negotiation(view_class: Any) -> Any:
    """
    None if ``view_class`` negotiates by hooks of its own
    (``perform_content_negotiation``, ...); otherwise whether a project's
    code runs between building DRF's request and negotiating.
    """
    if user_defines(view_class, *_CONTENT_NEGOTIATION_HOOKS):
        return None
    return user_defines(view_class, *_BEFORE_NEGOTIATION_HOOKS)


_EXCEPTION_HOOKS = (
    "get_exception_handler",
    "get_exception_handler_context",
)
# DRF's handler only builds a response.
register_pure(views.exception_handler)

# Conditional requests (``get_etag`` / ``get_last_modified``).
_VALIDATOR_HOOKS = (
    ("get_etag", "aget_etag"),
    ("get_last_modified", "aget_last_modified"),
)
_VALIDATOR_NAMES = frozenset(name for pair in _VALIDATOR_HOOKS for name in pair)


@class_cache
def _defines_validators(view_class: Any) -> bool:
    return any(
        resolve_pair(view_class, *pair) is not Impl.BASE for pair in _VALIDATOR_HOOKS
    )


_PRECONDITION_HEADERS = (
    "HTTP_IF_MATCH",
    "HTTP_IF_NONE_MATCH",
    "HTTP_IF_MODIFIED_SINCE",
    "HTTP_IF_UNMODIFIED_SINCE",
)
_SAFE_FOR_VALIDATORS = ("GET", "HEAD", "QUERY")

# A Structured Fields token (RFC 9651 section 3.3.4).
_SF_TOKEN = re.compile(r"[A-Za-z*][!#$%&'*+.^_`|~0-9A-Za-z:/-]*")


def _sf_string(value: Any) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def accept_query(media_types: Iterable[str]) -> str:
    """
    An ``Accept-Query`` field value (RFC 10008 section 3): the media types as a
    Structured Fields list, their parameters as parameters.
    """
    items = []
    for media_type in media_types:
        base, parameters = parse_header_parameters(media_type)
        item = base if _SF_TOKEN.fullmatch(base) else _sf_string(base)
        for key, value in parameters.items():
            item += f";{key}={_sf_string(value)}"
        items.append(item)
    return ", ".join(items)


@class_cache
def _builds_throttles_inline(view_class: Any, throttle_classes: Any) -> bool:
    if user_defines(view_class, "get_throttles"):
        return False
    return not any(
        user_defines(throttle_class, "__new__", "__init__", "get_rate", "parse_rate")
        for throttle_class in throttle_classes
    )


# What building an object runs of its class.
_CONSTRUCTORS = ("__new__", "__init__")


@class_cache
def _inline_checks(
    view_class: Any, hooks: Any, constructed: Any, called: Any = ()
) -> Any:
    """
    What decides whether a step of ``view_class``'s request runs on the event
    loop, worked out once per view class and configuration (the classes the
    view reads for the step, as they are on this request).

    None if the view overrides one of ``hooks``: the step runs in a thread.
    Otherwise the ``(object, method)`` pairs that must be pure: the
    constructors a project wrote among ``constructed`` (DRF's factories build
    those classes) and the methods in ``called``. Purity is asked on every request
    (``_holds``), so a declaration made later is seen at once.
    """
    if user_defines(view_class, *hooks):
        return None
    return (
        *(
            (cls, name)
            for cls in _permission_leaves(constructed)
            for name in _CONSTRUCTORS
            if user_defines(cls, name)
        ),
        *((obj, name) for obj, name in called if obj is not None),
    )


def _holds(checks: Any) -> bool:
    if not checks:
        return checks is not None
    return all(is_pure(obj, name) for obj, name in checks)


def _constructs_inline(*classes: Any) -> bool:
    """
    True if instantiating ``classes`` runs framework code only. DRF's
    factories (``get_parsers()``, ``get_permissions()``, ...) build objects
    from the configured classes; a constructor a project wrote for one of
    them may do anything, and then the factory runs in a thread.
    """
    return all(
        not user_defines(cls, name) or is_pure(cls, name)
        for cls in _permission_leaves(classes)
        for name in _CONSTRUCTORS
    )


@depends_on_classification
@class_cache
def _permission_plan(view_class: Any, classes: Any) -> bool | None:
    """
    Whether ``acheck_permissions`` can run DRF's loop over ``classes`` on the
    event loop, decided once per view class and permission classes: None when
    it cannot (a ``get_permissions`` or ``permission_denied`` of the
    project's, an operator, a constructor, a permission that is asynchronous
    or not pure); otherwise whether one may read ``request.user``.

    Dropped with the answers it is made of (``depends_on_classification``),
    so that ``register_pure`` and settings changes are seen at once.
    """
    if user_defines(view_class, "get_permissions", "permission_denied"):
        return None
    pair = ("has_permission", "ahas_permission")
    for cls in classes:
        if (
            not isinstance(cls, type)
            or user_defines(cls, *_CONSTRUCTORS)
            or not policies._permission_class_uses_sync(cls, *pair)
            or not is_pure(cls, pair[0])
        ):
            return None
    return any(policies._may_read_user(cls, *pair) for cls in classes)


@depends_on_classification
@class_cache
def _object_permission_plan(view_class: Any, classes: Any) -> bool:
    """
    Whether ``get_object`` can run DRF's ``check_object_permissions`` loop
    over ``classes`` itself, decided once per view class and permission
    classes: plain classes (no operators) without a constructor of the
    project's, whose ``has_object_permission`` is synchronous, and no
    ``get_permissions`` or ``permission_denied`` of the project's.

    Dropped with the answers it is made of (``depends_on_classification``).
    """
    if user_defines(view_class, "get_permissions", "permission_denied"):
        return False
    pair = ("has_object_permission", "ahas_object_permission")
    return all(
        isinstance(cls, type)
        and not user_defines(cls, *_CONSTRUCTORS)
        and policies._permission_class_uses_sync(cls, *pair)
        for cls in classes
    )


def _permission_leaves(classes: Any) -> Iterator[type]:
    # ``IsAuthenticated | ReadOnly`` in ``permission_classes`` is an operand
    # holder that instantiates both classes.
    for cls in classes:
        if isinstance(cls, permissions.OperandHolder):
            yield from _permission_leaves((cls.op1_class, cls.op2_class))
        elif isinstance(cls, permissions.SingleOperandHolder):
            yield from _permission_leaves((cls.op1_class,))
        else:
            yield cls


# -- The request plan -----------------------------------------------------------
#
# A view that leaves every step below to the framework, with DRF's own
# negotiation and framework parsers, renderers and authenticators, does on
# each request what its class and configuration determine. That is decided
# once per class (``_class_plan``); a request checks only that the view's
# configuration is still the one decided on (``_request_plan``). The steps
# build the same objects and set the same state as DRF's, without asking on
# every request which hooks are overridden and where they may run.

_PLANNED_HOOKS = (
    "dispatch",
    *_INITIALIZE_HOOKS,
    *_NEGOTIATION_HOOKS,
    "initial",
    "ainitial",
    "perform_authentication",
    "aperform_authentication",
    "check_permissions",
    "acheck_permissions",
    "get_permissions",
    "check_throttles",
    "acheck_throttles",
    "get_throttles",
    *(name for pair in _VALIDATOR_HOOKS for name in pair),
    "finalize_response",
    "afinalize_response",
    "get_renderer_context",
    "default_response_headers",
    "allowed_methods",
    "_allowed_methods",
)
_PLANNED_ATTRIBUTES = (
    "request_class",
    "parser_classes",
    "renderer_classes",
    "authentication_classes",
    "content_negotiation_class",
    "versioning_class",
    "http_method_names",
    "settings",
)
_PLAN_NAMES = frozenset((*_PLANNED_HOOKS, *_PLANNED_ATTRIBUTES))


def _snapshot(classes: Any) -> list[Any] | tuple[Any, ...]:
    # Of the declaration's type, so that a view declaring a tuple compares
    # equal to its plan (``() != []``), and a list edited later does not.
    return list(classes) if isinstance(classes, list) else tuple(classes)


class _RequestPlan:
    __slots__ = (
        "allow",
        "authentication_classes",
        "handler_names",
        "negotiations",
        "parser_classes",
        "renderer_classes",
        "request_class",
        "vary",
        "viewset",
    )

    def __init__(self, view_class: Any) -> None:
        self.request_class = view_class.request_class
        self.parser_classes = _snapshot(view_class.parser_classes)
        self.renderer_classes = _snapshot(view_class.renderer_classes)
        self.authentication_classes = _snapshot(view_class.authentication_classes)
        # (``Accept``, format) -> (the renderer's position, media type); the
        # renderers are the plan's, so they are not part of the key.
        self.negotiations: dict[tuple[str, str | None], tuple[int, str]] = {}
        # Django's ``_allowed_methods`` after ``View.setup``, which gives a
        # view with ``get`` a ``head``: the handlers of the class. A view
        # given handlers of its own (a viewset) is asked (``headers``).
        methods = view_class.http_method_names
        self.allow = ", ".join(
            method.upper()
            for method in methods
            if hasattr(view_class, method)
            or (method == "head" and hasattr(view_class, "get"))
        )
        self.handler_names = frozenset(methods) - {"head"}
        self.vary = len(self.renderer_classes) > 1
        from aiodrf.viewsets import ViewSetMixin

        self.viewset = issubclass(view_class, ViewSetMixin)

    def headers(self, view: Any) -> dict[str, str]:
        """DRF's ``default_response_headers``."""
        if not view.__dict__.keys().isdisjoint(self.handler_names):
            return view.default_response_headers
        headers = {"Allow": self.allow}
        if self.vary:
            headers["Vary"] = "Accept"
        return headers

    def initialize_request(self, view: Any, request: Any) -> Any:
        """DRF's ``initialize_request`` and the factories it calls."""
        negotiator = view.__dict__.get("_negotiator")
        if not negotiator:
            negotiator = view._negotiator = view.content_negotiation_class()
        drf_request = self.request_class(
            request,
            parsers=[parser() for parser in self.parser_classes],
            authenticators=[
                authenticator() for authenticator in self.authentication_classes
            ],
            negotiator=negotiator,
            parser_context={"view": view, "args": view.args, "kwargs": view.kwargs},
        )
        if self.viewset:
            # aiodrf's ``ViewSetMixin.initialize_request``: ``action`` from the
            # ``action_map`` the view function set (read as DRF reads it).
            method = request.method.lower()
            view.action = (
                "metadata" if method == "options" else view.action_map.get(method)
            )
        return drf_request

    async def initial(self, view: Any, request: Any, kwargs: Any) -> None:
        """DRF's ``initial``: negotiation, then authentication, permissions, throttles."""
        suffix = view.settings.FORMAT_SUFFIX_KWARG
        view.format_kwarg = kwargs.get(suffix) if suffix else None
        request.accepted_renderer, request.accepted_media_type = self.negotiate(
            view, request
        )
        request.version, request.versioning_scheme = None, None

        # ``request.user``, on a request no code has seen yet.
        if request.authenticators:
            await request._aauthenticate()
        else:
            request._not_authenticated()
        if view.permission_classes:
            await view.acheck_permissions(request)
        if view.throttle_classes:
            await view.acheck_throttles(request)

    def negotiate(self, view: Any, request: Any) -> tuple[Any, str]:
        """DRF's ``perform_content_negotiation``, kept per ``Accept`` and format."""
        accept = _accept_header(request, False)
        format_override = DefaultContentNegotiation.settings.URL_FORMAT_OVERRIDE
        wanted = view.format_kwarg or _query_param(request, format_override)
        kept = self.negotiations.get((accept, wanted))
        if kept is not None:
            # The request's own renderer, of the class DRF's would select.
            return self.renderer_classes[kept[0]](), kept[1]
        # A failure (406, or 404 for an unknown format) is raised, not kept.
        renderer, media_type = view._perform_content_negotiation(request, False)
        if len(accept) <= _MAX_KEPT_ACCEPT:
            index = self.renderer_classes.index(type(renderer))
            _keep_negotiation(self.negotiations, (accept, wanted), (index, media_type))
        return renderer, media_type


@class_cache
def _class_plan(view_class: Any) -> _RequestPlan | None:
    # The steps must be APIView's own: another framework class may override
    # one as well (``ViewSetMixin.initialize_request`` sets ``action``, a
    # tracing mixin wraps them).
    from aiodrf.viewsets import ViewSetMixin

    # aiodrf's ``ViewSetMixin.initialize_request`` sets ``action``, which the
    # plan's does too (DRF's own mixin, found first, keeps the generic path).
    definers = _PLAN_DEFINERS | {ViewSetMixin}
    mro = view_class.__mro__
    for name in _PLANNED_HOOKS:
        owner = next((klass for klass in mro if name in klass.__dict__), None)
        if owner not in definers:
            return None
    if view_class.versioning_class is not None or not (
        view_class.content_negotiation_class is DefaultContentNegotiation
        and (
            DefaultContentNegotiation.select_renderer,
            DefaultContentNegotiation.filter_renderers,
            DefaultContentNegotiation.get_accept_list,
        )
        == _DRF_NEGOTIATION
    ):
        return None
    request_class = view_class.request_class
    if not (
        isinstance(request_class, type)
        and issubclass(request_class, Request)
        and is_framework_class(request_class)
    ):
        return None
    classes = (
        *view_class.parser_classes,
        *view_class.renderer_classes,
        *view_class.authentication_classes,
    )
    # Framework classes: building one runs no code of the project's.
    if not view_class.renderer_classes or not all(
        isinstance(cls, type) and is_framework_class(cls) for cls in classes
    ):
        return None
    return _RequestPlan(view_class)


def _request_plan(view: Any) -> _RequestPlan | None:
    """The plan of ``view``'s class, if the view is still configured as it."""
    plan = _class_plan(type(view))
    if (
        plan is None
        or not view.__dict__.keys().isdisjoint(_PLAN_NAMES)
        or view.parser_classes != plan.parser_classes
        or view.renderer_classes != plan.renderer_classes
        or view.authentication_classes != plan.authentication_classes
    ):
        return None
    return plan


async def _aensure_user(request: Any) -> None:
    # DRF's ``request.user``: authenticate unless a user is set, by the
    # authenticators or by the view's own code (``request.user = ...``).
    if not hasattr(request, "_user") and hasattr(request, "_aauthenticate"):
        await request._aauthenticate()


async def _aensure_authenticator(request: Any) -> None:
    # DRF's ``request.successful_authenticator``, which a user set by code
    # does not provide.
    if not hasattr(request, "_authenticator") and hasattr(request, "_aauthenticate"):
        await request._aauthenticate()


@bridge_base
class APIView(views.APIView):
    #: The request wrapper built by ``initialize_request``.
    request_class = Request
    #: Validates ``request.query_params``; see ``aget_validated_query_params``.
    query_serializer_class: type[serializers.BaseSerializer] | None = None

    if not DJANGO_HAS_QUERY:
        # TODO(django#37232): inherited from Django's ``View`` once it has QUERY.
        http_method_names = [*views.APIView.http_method_names, "query"]

    @classmethod
    def as_view(cls, *args: Any, **initkwargs: Any) -> Any:
        # A classmethod, as in DRF. Viewset MROs pass the action mapping to DRF's ViewSetMixin.
        check_view_hooks(cls, initkwargs)
        cls._compile_serializers(initkwargs)
        return super().as_view(*args, **initkwargs)

    @classmethod
    def _compile_serializers(cls, initkwargs: dict[str, Any]) -> None:
        # The static serializer, resolved and checked once per URL
        # (``aiodrf.backends``); ``SchemaViewMixin`` adds its schemas.
        serializer_class = initkwargs.get(
            "serializer_class", getattr(cls, "serializer_class", None)
        )
        if serializer_class is not None:
            compile_serializer(serializer_class, cls)

    @classproperty
    def view_is_async(cls) -> bool:
        # ``dispatch`` is always a coroutine, whatever the handlers are, so
        # Django must treat the view as async. Returning True unconditionally
        # also stops Django from rejecting classes that mix sync and async
        # handlers; aiodrf runs sync handlers in a thread.
        return True

    def initialize_request(
        self, request: HttpRequest, *args: Any, **kwargs: Any
    ) -> Request:
        parser_context = self.get_parser_context(request)

        return self.request_class(
            request,
            parsers=self.get_parsers(),
            authenticators=self.get_authenticators(),
            negotiator=self.get_content_negotiator(),
            parser_context=parser_context,
        )

    # -- Query parameters --------------------------------------------------------

    def get_query_serializer_class(self) -> type[serializers.BaseSerializer]:
        """The serializer that validates the query string, like ``get_serializer_class``."""
        assert self.query_serializer_class is not None, (  # noqa: S101 -- as in DRF
            "'%s' should either include a `query_serializer_class` attribute, or "
            "override the `get_query_serializer_class()` method."
            % self.__class__.__name__
        )
        return self.query_serializer_class

    def get_query_serializer(
        self, *args: Any, **kwargs: Any
    ) -> serializers.BaseSerializer:
        serializer_class = self.get_query_serializer_class()
        kwargs.setdefault(
            "context",
            {"request": self.request, "format": self.format_kwarg, "view": self},
        )
        return serializer_class(*args, **kwargs)

    @bridges_to("aget_validated_query_params")
    def get_validated_query_params(self) -> Any:
        """
        ``request.query_params`` validated by ``get_query_serializer()``: its
        ``validated_data``, or ``ValidationError`` (a 400 response).
        Validated once per request.
        """
        if "_validated_query_params" not in self.__dict__:
            serializer, valid = self._validate_query_params()
            if valid is aio.NEEDS_AWAIT:
                async_to_sync(aio.is_valid)(serializer, raise_exception=True)
            self._validated_query_params = serializer.validated_data
        return self._validated_query_params

    async def aget_validated_query_params(self) -> Any:
        """Async counterpart of ``get_validated_query_params``."""
        pair = ("get_validated_query_params", "aget_validated_query_params")
        if resolve_pair(type(self), *pair) is Impl.SYNC:
            return await run_sync(self.get_validated_query_params)()
        if "_validated_query_params" not in self.__dict__:
            if user_defines(
                self, "get_query_serializer", "get_query_serializer_class"
            ) or not is_declarative_class(self.get_query_serializer_class()):
                # Built by the project's code: in the worker, with the validation.
                serializer, valid = await run_sync(self._validate_query_params)()
                if valid is aio.NEEDS_AWAIT:
                    await aio.is_valid(serializer, raise_exception=True)
            else:
                serializer = self.get_query_serializer(data=self.request.query_params)
                await aio.is_valid(serializer, raise_exception=True)
            self._validated_query_params = serializer.validated_data
        return self._validated_query_params

    def _validate_query_params(self) -> Any:
        serializer = self.get_query_serializer(data=self.request.query_params)
        return serializer, aio.try_is_valid(serializer, raise_exception=True)

    # -- Conditional requests ------------------------------------------------------

    @bridges_to("aget_etag")
    def get_etag(self, request: Request, *args: Any, **kwargs: Any) -> str | None:
        """
        The ETag of the requested resource, or None: what ``etag_func`` of
        Django's ``condition()`` returns, quoted or not. Called after
        authentication, permissions and throttling, before the handler.
        """
        return None

    async def aget_etag(
        self, request: Request, *args: Any, **kwargs: Any
    ) -> str | None:
        """Async counterpart of ``get_etag``."""
        return None

    @bridges_to("aget_last_modified")
    def get_last_modified(
        self, request: Request, *args: Any, **kwargs: Any
    ) -> datetime.datetime | None:
        """
        When the requested resource last changed, or None: what
        ``last_modified_func`` of Django's ``condition()`` returns.
        """
        return None

    async def aget_last_modified(
        self, request: Request, *args: Any, **kwargs: Any
    ) -> datetime.datetime | None:
        """Async counterpart of ``get_last_modified``."""
        return None

    async def _avalidators(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """
        The ETag and Last-Modified value of the resource, as Django's
        ``condition()`` computes them; None when the view defines neither
        hook or the request cannot use them (a write without preconditions).
        """
        if not _defines_validators(type(self)) and self.__dict__.keys().isdisjoint(
            _VALIDATOR_NAMES
        ):
            return None
        impls = {resolve_pair(self, *pair) for pair in _VALIDATOR_HOOKS}
        if request.method not in _SAFE_FOR_VALIDATORS and not any(
            header in request.META for header in _PRECONDITION_HEADERS
        ):
            return None
        if Impl.SYNC in impls and not impls & {Impl.ASYNC, Impl.SYNC_IS_ASYNC}:
            # Code written for DRF: both hooks in one hop.
            etag, last_modified = await run_sync(self._validators)(
                request, *args, **kwargs
            )
        else:
            etag, last_modified = [
                await call_pair(self, *pair, request, *args, **kwargs)
                for pair in _VALIDATOR_HOOKS
            ]
        if etag is not None:
            etag = quote_etag(etag)
        if last_modified:
            if not timezone.is_aware(last_modified):
                last_modified = timezone.make_aware(last_modified, datetime.UTC)
            last_modified = int(last_modified.timestamp())
        return etag, last_modified

    def _validators(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        return (
            self.get_etag(request, *args, **kwargs),
            self.get_last_modified(request, *args, **kwargs),
        )

    async def ainitial(self, request: Request, *args: Any, **kwargs: Any) -> None:
        """Async counterpart of ``initial``."""
        if self._negotiates_inline():
            self._negotiate(request, *args, **kwargs)
        else:
            await run_sync(self._negotiate)(request, *args, **kwargs)

        # Ensure that the incoming request is permitted
        await call_pair(
            self, "perform_authentication", "aperform_authentication", request
        )
        await call_pair(self, "check_permissions", "acheck_permissions", request)
        await call_pair(self, "check_throttles", "acheck_throttles", request)

    def _negotiate(self, request: Any, *args: Any, **kwargs: Any) -> None:
        # The first half of DRF's ``initial``.
        self.format_kwarg = self.get_format_suffix(**kwargs)

        # Perform content negotiation and store the accepted info on the request
        project_code_ran = _negotiation(type(self))
        if project_code_ran is None:
            neg = self.perform_content_negotiation(request)
        else:
            neg = self._perform_content_negotiation(request, project_code_ran)
        request.accepted_renderer, request.accepted_media_type = neg

        # Determine the API version, if versioning is in use.
        version, scheme = self.determine_version(request, *args, **kwargs)
        request.version, request.versioning_scheme = version, scheme

    def _perform_content_negotiation(
        self, request: Any, project_code_ran: Any
    ) -> tuple[Any, str]:
        """
        DRF's ``perform_content_negotiation``. What DRF's negotiation selects
        depends on the ``Accept`` header, the format asked for and the
        renderers' media types and formats only; it is kept, as the
        renderer's position and the media type, and the renderers are the
        request's own.
        """
        renderers = self.get_renderers()
        negotiator = self.get_content_negotiator()
        if not (_is_drf_negotiation(negotiator) and isinstance(request, DRFRequest)):
            return negotiator.select_renderer(request, renderers, self.format_kwarg)
        # Read as DRF reads them (``select_renderer``, ``get_accept_list``).
        accept = _accept_header(request, project_code_ran)
        format_override = DefaultContentNegotiation.settings.URL_FORMAT_OVERRIDE
        wanted = self.format_kwarg or _query_param(request, format_override)
        key = (
            accept,
            wanted,
            *[(renderer.media_type, renderer.format) for renderer in renderers],
        )
        kept = _negotiations.get(key)
        if kept is not None:
            return renderers[kept[0]], kept[1]
        # A failure (406, or 404 for an unknown format) is raised, not kept.
        renderer, media_type = negotiator.select_renderer(
            request, renderers, self.format_kwarg
        )
        if len(accept) <= _MAX_KEPT_ACCEPT:
            for index, candidate in enumerate(renderers):
                if candidate is renderer:
                    _keep_negotiation(_negotiations, key, (index, media_type))
                    break
        return renderer, media_type

    # The classes are read from the view on every request, as DRF does: an
    # ``as_view()`` argument, an attribute set on the instance or changed on
    # the class is a configuration of its own.

    def _negotiates_inline(self) -> bool:
        negotiation = self.content_negotiation_class
        return _holds(
            _inline_checks(
                type(self),
                _NEGOTIATION_HOOKS,
                (*self.renderer_classes, negotiation),
                (
                    (negotiation, "select_renderer"),
                    (self.versioning_class, "determine_version"),
                ),
            )
        )

    def _initializes_inline(self) -> bool:
        return _holds(
            _inline_checks(
                type(self),
                _INITIALIZE_HOOKS,
                (
                    self.request_class,
                    *self.parser_classes,
                    *self.authentication_classes,
                    self.content_negotiation_class,
                ),
            )
        )

    # DRF's synchronous checks, used where DRF works synchronously (an
    # ``initial()`` override calling ``super()``, DRF's ``get_object``, schema
    # generation). They must reach policies that only implement the async
    # member, whatever base class those derive from, and a view that only
    # overrides the async hook.

    @bridges_to("acheck_permissions")
    def check_permissions(self, request: DRFRequest) -> None:
        for permission in self.get_permissions():
            if not policies.has_permission(permission, request, self):
                self.permission_denied(
                    request,
                    message=getattr(permission, "message", None),
                    code=getattr(permission, "code", None),
                )

    @bridges_to("acheck_object_permissions")
    def check_object_permissions(self, request: DRFRequest, obj: Any) -> None:
        for permission in self.get_permissions():
            if not policies.has_object_permission(permission, request, self, obj):
                self.permission_denied(
                    request,
                    message=getattr(permission, "message", None),
                    code=getattr(permission, "code", None),
                )

    @bridges_to("acheck_throttles")
    def check_throttles(self, request: DRFRequest) -> None:
        throttle_durations = [
            throttle.wait()
            for throttle in self.get_throttles()
            if not policies.allow_request(throttle, request, self)
        ]
        if throttle_durations:
            durations = [
                duration for duration in throttle_durations if duration is not None
            ]
            self.throttled(request, max(durations, default=None))  # type: ignore[arg-type]

    async def aperform_authentication(self, request: Request) -> None:
        if isinstance(request, Request):
            await request.auser()
        else:
            await run_sync(lambda: request.user)()

    async def acheck_permissions(self, request: Request) -> None:
        classes = self.permission_classes
        if not classes and not user_defines(self, "get_permissions"):
            return  # DRF's loop over no permissions
        state = self.__dict__
        if (
            type(classes) in (list, tuple)
            and "get_permissions" not in state
            and "permission_denied" not in state
        ):
            reads_user = _permission_plan(type(self), tuple(classes))
            if reads_user is not None:
                # DRF's ``get_permissions()``; a permission given state by its
                # class (the plan excludes constructors) takes DRF's path.
                permissions = [permission() for permission in classes]
                if not any(vars(permission) for permission in permissions):
                    if reads_user:
                        await _aensure_user(request)
                    for permission in permissions:
                        if not permission.has_permission(request, self):
                            await _aensure_authenticator(request)
                            self.permission_denied(
                                request,
                                message=getattr(permission, "message", None),
                                code=getattr(permission, "code", None),
                            )
                    return
        await self._acheck(
            request,
            (("has_permission", "ahas_permission"),),
            lambda permission: policies.ahas_permission(permission, request, self),
            lambda permission: policies.has_permission(permission, request, self),
        )

    async def acheck_object_permissions(self, request: Request, obj: Any) -> None:
        await self._acheck(
            request,
            (
                ("has_permission", "ahas_permission"),
                ("has_object_permission", "ahas_object_permission"),
            ),
            lambda permission: policies.ahas_object_permission(
                permission, request, self, obj
            ),
            lambda permission: policies.has_object_permission(
                permission, request, self, obj
            ),
        )

    async def _acheck(self, request: Any, pairs: Any, acheck: Any, check: Any) -> None:
        def deny(permission: Any) -> None:
            self.permission_denied(
                request,
                message=getattr(permission, "message", None),
                code=getattr(permission, "code", None),
            )

        def check_now(permissions: Any) -> Any:
            """
            In a thread: DRF's loop, unless a permission has to be awaited,
            in which case the permissions go back to the event loop.
            """
            if permissions is None:
                permissions = self.get_permissions()
                if policies.permissions_mode(permissions, *pairs) is Mode.ASYNC:
                    return permissions
            for permission in permissions:
                if not check(permission):
                    deny(permission)
            return None

        if not _holds(
            _inline_checks(
                type(self), ("get_permissions",), tuple(self.permission_classes)
            )
        ):
            # Written for DRF, so it may query: it runs in the thread that
            # goes on to check what it returned.
            permissions = await run_sync(check_now)(None)
            if permissions is None:
                return
            mode = Mode.ASYNC
        else:
            permissions = self.get_permissions()
            if type(permissions) in (list, tuple) and not permissions:
                return
            mode = policies.permissions_mode(permissions, *pairs)
            if mode is Mode.THREAD:
                await run_sync(check_now)(permissions)
                return

        # Reading ``request.user`` of a lazily authenticated request runs the
        # authenticators synchronously, which the event loop cannot do.
        # Authenticate first, but only if a permission can read the user:
        # DRF never authenticates a request nothing asks about.
        if policies.reads_user(permissions, *pairs):
            await _aensure_user(request)

        # DRF's loop: every permission that denies goes to permission_denied(),
        # which raises by default; an override may return and let the next
        # permission decide.
        for permission in permissions:
            allowed = (
                check(permission) if mode is Mode.INLINE else await acheck(permission)
            )
            if allowed:
                continue
            # ``permission_denied`` reads ``request.successful_authenticator``.
            await _aensure_authenticator(request)
            if user_defines(self, "permission_denied"):
                # Written for DRF (an audit log, a 404 instead of a 403).
                await run_sync(deny)(permission)
            else:
                deny(permission)

    async def acheck_throttles(self, request: Request) -> None:
        if not self.throttle_classes and not user_defines(self, "get_throttles"):
            return  # DRF's loop over no throttles

        def durations_of_failed(
            throttles: Any,
        ) -> tuple[Any, list[float | None] | None]:
            """In a thread: build the throttles if need be, and ask them."""
            if throttles is None:
                throttles = self.get_throttles()
                if policies.throttles_mode(throttles) is Mode.ASYNC:
                    return throttles, None
            return None, [
                throttle.wait()
                for throttle in throttles
                if not policies.allow_request(throttle, request, self)
            ]

        if _builds_throttles_inline(type(self), tuple(self.throttle_classes)):
            throttles = self.get_throttles()
            if not throttles:
                return
            mode = policies.throttles_mode(throttles)
        else:
            # ``SimpleRateThrottle.__init__`` calls ``get_rate()``: building a
            # project's throttle is running its code.
            throttles, mode = None, Mode.THREAD

        if mode is Mode.THREAD:
            throttles, throttle_durations = await run_sync(durations_of_failed)(
                throttles
            )
            if throttles is not None:
                mode = Mode.ASYNC
        if mode is not Mode.THREAD:
            # DRF's rate throttles read ``request.user`` for their cache key.
            await _aensure_user(request)
            throttle_durations = []
            for throttle in throttles or ():
                if mode is Mode.INLINE:
                    allowed = policies.allow_request(throttle, request, self)
                else:
                    allowed = await policies.aallow_request(throttle, request, self)
                if not allowed:
                    throttle_durations.append(await policies._throttle_wait(throttle))

        if throttle_durations:
            # Filter out `None` values which may happen in case of config / rate
            # changes, see DRF #1438
            durations = [
                duration for duration in throttle_durations if duration is not None
            ]

            duration = max(durations, default=None)
            # DRF accepts None: no Retry-After header.
            if user_defines(self, "throttled"):
                await run_sync(self.throttled)(request, duration)  # type: ignore[arg-type]
            else:
                self.throttled(request, duration)  # type: ignore[arg-type]

    @bridges_to("aget_authenticate_header")
    def get_authenticate_header(self, request: Any) -> str | None:
        authenticators = self.get_authenticators()
        if authenticators:
            return call_pair_sync(
                authenticators[0],
                "authenticate_header",
                "aauthenticate_header",
                request,
            )
        return None

    async def aget_authenticate_header(self, request: Request) -> str | None:
        """Ask the first authenticator for its challenge, as DRF does."""
        if user_defines(self, "get_authenticators") or not _constructs_inline(
            *self.authentication_classes
        ):
            authenticators = await run_sync(self.get_authenticators)()
        else:
            authenticators = self.get_authenticators()
        if not authenticators:
            return None
        authenticator = authenticators[0]
        pair = ("authenticate_header", "aauthenticate_header")
        impl = resolve_pair(authenticator, *pair)
        if impl not in (Impl.ASYNC, Impl.SYNC_IS_ASYNC) and (
            not user_defines(authenticator, pair[0]) or is_pure(authenticator, pair[0])
        ):
            return authenticator.authenticate_header(request)
        return await call_pair(authenticator, *pair, request)

    async def ahandle_exception(self, exc: Exception) -> HttpResponseBase:
        """Async counterpart of ``handle_exception``."""
        if isinstance(
            exc, (exceptions.NotAuthenticated, exceptions.AuthenticationFailed)
        ):
            auth_header = await call_pair(
                self,
                "get_authenticate_header",
                "aget_authenticate_header",
                self.request,
            )
            if auth_header:
                exc.auth_header = auth_header  # type: ignore[union-attr]
            else:
                exc.status_code = status.HTTP_403_FORBIDDEN

        if user_defines(self, *_EXCEPTION_HOOKS):
            exception_handler, context = await run_sync(self._exception_handler)()
        else:
            exception_handler, context = self._exception_handler()

        if awaits_inline(exception_handler):
            response = await exception_handler(exc, context)
        elif is_pure_function(exception_handler):
            response = await maybe_await(exception_handler(exc, context))
        else:
            # A sync wrapper of an async handler returns what is awaited here.
            response = await run_sync_and_await(exception_handler, exc, context)

        if response is None:
            self.raise_uncaught_exception(exc)

        response.exception = True
        return response

    def _exception_handler(self) -> Any:
        return self.get_exception_handler(), self.get_exception_handler_context()

    @bridges_to("afinalize_response")
    def finalize_response(
        self, request: Any, response: Any, *args: Any, **kwargs: Any
    ) -> Any:
        return self._finalize_response(request, response, *args, **kwargs)

    def _finalize_response(
        self, request: Any, response: Any, *args: Any, **kwargs: Any
    ) -> Any:
        if isinstance(response, DataResponse) and response.renderer_context is None:
            response = resolve_data_response(response, self, request)
        return super().finalize_response(
            request,
            upgrade_response(response),  # type: ignore[arg-type]  # DRF accepts HttpResponseBase
            *args,
            **kwargs,
        )

    async def afinalize_response(
        self, request: Request, response: HttpResponseBase, *args: Any, **kwargs: Any
    ) -> HttpResponseBase:
        """Finalize without re-entering an override through its synchronous bridge."""
        if isinstance(response, DataResponse) and response.renderer_context is None:
            response = await aresolve_data_response(
                response,
                self,
                request,
                inline=not user_defines(self, "get_renderer_context"),
            )
        if user_defines(self, "get_renderer_context") or (
            not getattr(request, "accepted_renderer", None)
            and not self._negotiates_inline()
        ):
            return await run_sync(self._finalize_response)(
                request, response, *args, **kwargs
            )
        return self._finalize_response(request, response, *args, **kwargs)

    async def dispatch(
        self, request: Any, *args: Any, **kwargs: Any
    ) -> HttpResponseBase:
        """
        Async counterpart of DRF's ``dispatch``.

        ``async def`` handlers are awaited on the event loop; synchronous ones
        run in a thread.
        """
        self.args = args
        self.kwargs = kwargs
        plan = _request_plan(self)
        if plan is not None:
            request = plan.initialize_request(self, request)
        elif self._initializes_inline():
            request = self.initialize_request(request, *args, **kwargs)
        else:
            request = await run_sync(self.initialize_request)(request, *args, **kwargs)
        self.request = request
        if plan is not None:
            self.headers = plan.headers(self)
        else:
            self.headers = self.default_response_headers  # deprecate?

        try:
            if plan is not None:
                await plan.initial(self, request, kwargs)
            else:
                await call_pair(self, "initial", "ainitial", request, *args, **kwargs)

            # Get the appropriate handler method
            method = request.method
            if method.lower() in self.http_method_names:
                handler = getattr(self, method.lower(), self.http_method_not_allowed)
            else:
                handler = self.http_method_not_allowed

            if plan is not None and method != "QUERY":
                # No preconditions: the plan's view defines no validators.
                response = await self.acall_handler(handler, request, *args, **kwargs)
            else:
                response = await self._acall_conditionally(
                    handler, request, *args, **kwargs
                )

        except Exception as exc:  # noqa: BLE001 -- as in DRF's dispatch
            response = await call_pair(
                self, "handle_exception", "ahandle_exception", exc
            )

        if plan is not None:
            if isinstance(response, DataResponse) and response.renderer_context is None:
                response = await aresolve_data_response(response, self, request)
            self.response = self._finalize_response(request, response, *args, **kwargs)
        else:
            self.response = await call_pair(
                self,
                "finalize_response",
                "afinalize_response",
                request,
                response,
                *args,
                **kwargs,
            )
        return self.response

    async def _acall_conditionally(
        self, handler: Any, request: Any, *args: Any, **kwargs: Any
    ) -> HttpResponseBase:
        """Call the handler unless the request's preconditions answer it (304, 412)."""
        if handler == self.http_method_not_allowed:
            return await self.acall_handler(handler, request, *args, **kwargs)
        if request.method == "QUERY":
            try:
                await self._acheck_query_content(request)
            except exceptions.UnsupportedMediaType as exc:
                response = await call_pair(
                    self, "handle_exception", "ahandle_exception", exc
                )
                response["Accept-Query"] = accept_query(
                    p.media_type for p in request.parsers
                )
                return response
        validators = await self._avalidators(request, *args, **kwargs)
        if validators is None:
            return await self.acall_handler(handler, request, *args, **kwargs)
        etag, last_modified = validators
        conditional_request = request._request
        if request.method == "QUERY" and not DJANGO_HAS_QUERY:
            # TODO(django#37232): Django evaluates QUERY like GET (RFC 10008 section 2.6).
            conditional_request = copy.copy(conditional_request)
            conditional_request.method = "GET"
        # Django's RFC 9110 evaluation, as in its ``condition()`` decorator.
        response = get_conditional_response(
            conditional_request, etag=etag, last_modified=last_modified
        )
        if (
            response is not None
            and response.status_code == status.HTTP_412_PRECONDITION_FAILED
        ):
            raise PreconditionFailed
        if response is None:
            response = await self.acall_handler(handler, request, *args, **kwargs)
        if request.method in _SAFE_FOR_VALIDATORS:
            if last_modified and not response.has_header("Last-Modified"):
                response.headers["Last-Modified"] = http_date(last_modified)
            if etag:
                response.headers.setdefault("ETag", etag)
        return response

    async def _acheck_query_content(self, request: Any) -> None:
        """
        RFC 10008 sections 2 and 2.1: the content of a QUERY request is the query,
        so it must be there, have a media type and parse as that type.
        Parsing it here fails the request before the handler runs: 400,
        or 415 when no parser accepts the media type.
        """
        if not request.content_type:
            raise exceptions.ParseError("A QUERY request needs a Content-Type.")
        if request.stream is None:
            raise exceptions.ParseError("A QUERY request needs content.")
        await request.adata()

    async def acall_handler(
        self, handler: Callable[..., Any], request: Request, *args: Any, **kwargs: Any
    ) -> HttpResponseBase:
        if awaits_inline(handler):
            return await handler(request, *args, **kwargs)
        if is_pure_function(getattr(handler, "__func__", handler)):
            return await maybe_await(handler(request, *args, **kwargs))
        # A sync handler, or a sync wrapper that returns a coroutine (for
        # example a handler of a DRF concrete view returning ``self.list()``,
        # or a decorator around an ``async def`` handler): its own code runs
        # in a worker, the coroutine on the event loop.
        return await run_sync_and_await(handler, request, *args, **kwargs)

    @async_safe
    def http_method_not_allowed(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        return super().http_method_not_allowed(request, *args, **kwargs)

    async def options(
        self, request: Any, *args: Any, **kwargs: Any
    ) -> HttpResponseBase:
        """
        Handler method for HTTP 'OPTIONS' request.

        Metadata classes inspect serializers and may call ``get_object()``,
        so they run in a thread.
        """
        if self.metadata_class is None:
            return self.http_method_not_allowed(request, *args, **kwargs)

        def determine_metadata() -> Any:
            metadata = self.metadata_class()  # type: ignore[misc,operator]  # a class; the stubs allow a dotted path
            require_sync_hooks(
                metadata,
                "determine_metadata",
                "determine_actions",
                "get_serializer_info",
                "get_field_info",
            )
            return metadata.determine_metadata(request, self)

        data = await run_sync(determine_metadata)()
        response = Response(data, status=status.HTTP_200_OK)
        if hasattr(self, "query"):
            response["Accept-Query"] = accept_query(
                p.media_type for p in request.parsers
            )
        return response


# Where the planned steps of a view are defined when it leaves them to the
# framework (``_class_plan``).
_PLAN_DEFINERS = frozenset({View, views.APIView, APIView})
