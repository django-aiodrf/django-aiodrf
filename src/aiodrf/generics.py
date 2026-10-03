"""
Async generic views.

The concrete views subclass both aiodrf's and DRF's classes, so
``isinstance(view, rest_framework.generics.ListCreateAPIView)`` holds, which
drf-spectacular relies on to document list and create operations.

Class documentation in this module is written as comments: drf-spectacular
publishes the docstrings of view classes it does not recognise as DRF's own
as API descriptions.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from django.db.models import Model, QuerySet
from django.http import HttpResponseBase
from fastdrf._compiled import FIELDS_FROM_CLASS
from fastdrf.prefetch import auto_prefetch
from fastdrf.settings import fastdrf_settings
from rest_framework import generics, serializers
from rest_framework.generics import get_object_or_404
from rest_framework.serializers import BaseSerializer

from aiodrf import aio, mixins, policies
from aiodrf.aio._classify import is_declarative_class
from aiodrf.aio._loaded import reads_loaded
from aiodrf.aio._represent import _uses_compiler
from aiodrf.backends import compile_serializer
from aiodrf.compat import FETCH_PEERS, FETCH_RAISE
from aiodrf.policies import Mode
from aiodrf.request import Request
from aiodrf.response import Response
from aiodrf.settings import aiodrf_settings
from aiodrf.utils import (
    Impl,
    bridge_base,
    bridges_to,
    call_pair,
    call_pair_sync,
    resolve_pair,
    run_sync,
    user_defines,
    uses_sync,
)
from aiodrf.views import APIView, _object_permission_plan

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
    "get_object_or_404",
]

for _base in (
    generics.GenericAPIView,
    generics.CreateAPIView,
    generics.ListAPIView,
    generics.RetrieveAPIView,
    generics.DestroyAPIView,
    generics.UpdateAPIView,
    generics.ListCreateAPIView,
    generics.RetrieveUpdateAPIView,
    generics.RetrieveDestroyAPIView,
    generics.RetrieveUpdateDestroyAPIView,
):
    bridge_base(_base)

_FETCH_MODES = {"peers": FETCH_PEERS, "raise": FETCH_RAISE}

_QUERYSET = ("get_queryset", "aget_queryset")
_FILTER = ("filter_queryset", "afilter_queryset")
_PAGINATE = ("paginate_queryset", "apaginate_queryset")
_CHECK_OBJECT = ("check_object_permissions", "acheck_object_permissions")
_SERIALIZER_FACTORIES = (
    ("get_serializer", "aget_serializer"),
    ("get_serializer_class", "aget_serializer_class"),
    ("get_serializer_context", "aget_serializer_context"),
)


def _list_class(serializer_class: Any) -> Any:
    """The list serializer class ``serializer_class.many_init`` instantiates."""
    meta = getattr(serializer_class, "Meta", None)
    return (
        getattr(meta, "list_serializer_class", None)
        or getattr(serializer_class, "default_list_serializer_class", None)
        or serializers.ListSerializer
    )


_SERIALIZER_FACTORY_NAMES = tuple(
    name for pair in _SERIALIZER_FACTORIES for name in pair
)
_OBJECT_PERMISSIONS = (
    ("has_permission", "ahas_permission"),
    ("has_object_permission", "ahas_object_permission"),
)


@bridge_base
class GenericAPIView[ModelT: Model](APIView, generics.GenericAPIView):
    # DRF's ``GenericAPIView`` with async counterparts of the methods that
    # reach the database: ``aget_object``, ``afilter_queryset``,
    # ``apaginate_queryset`` and ``aget_paginated_response``.
    #
    # The synchronous work of an action (``get_queryset``, filtering, the
    # lookup or the page, ``get_serializer``, validation, the save,
    # representation) runs in one thread hop, with DRF's own code, so
    # overrides written for DRF may query wherever they like. The hop ends
    # early only where something has to be awaited: an ``aget_queryset``,
    # async filter backends or paginators, async object permissions, an
    # ``aperform_*`` hook, a serializer with async members.

    @bridges_to("aget_queryset")
    def get_queryset(self) -> QuerySet[ModelT]:
        # Sync callers (DjangoModelPermissions, DRF's ``get_object``, the
        # browsable API, drf-spectacular) must see a queryset that is only
        # scoped by ``aget_queryset``.
        return super().get_queryset()

    # Sync callers of the other pairs: the browsable API, ``OPTIONS``
    # metadata and overrides written for DRF that call ``super()``.

    @bridges_to("aget_object")
    def get_object(self) -> ModelT:
        return super().get_object()

    @bridges_to("afilter_queryset")
    def filter_queryset(self, queryset: QuerySet[ModelT]) -> QuerySet[ModelT]:
        for backend in list(self.filter_backends):
            queryset = policies.filter_queryset(backend(), self.request, queryset, self)
        return queryset

    @bridges_to("apaginate_queryset")
    def paginate_queryset(
        self, queryset: QuerySet[Any] | Sequence[Any]
    ) -> list[Any] | None:
        if self.paginator is None:
            return None
        return call_pair_sync(
            self.paginator,
            "paginate_queryset",
            "apaginate_queryset",
            queryset,
            self.request,
            view=self,
        )

    @bridges_to("aget_paginated_response")
    def get_paginated_response(self, data: Any) -> Response:
        pair = ("get_paginated_response", "aget_paginated_response")
        assert self.paginator is not None  # noqa: S101 -- as in DRF
        return call_pair_sync(self.paginator, *pair, data)

    async def aget_queryset(self) -> QuerySet[ModelT]:
        """Async counterpart of ``get_queryset``."""
        # DRF's implementation, not ``self.get_queryset()``: that would
        # re-enter the bridge above from ``await super().aget_queryset()``.
        return generics.GenericAPIView.get_queryset(self)

    def _awaits(self, sync_name: str, async_name: str) -> bool:
        """True if the view implements this pair with code that must be awaited."""
        impl = resolve_pair(self, sync_name, async_name)
        return impl in (Impl.ASYNC, Impl.SYNC_IS_ASYNC)

    def _awaits_serializer(self) -> bool:
        return any(self._awaits(*pair) for pair in _SERIALIZER_FACTORIES)

    def _awaits_filtering(self) -> bool:
        return self._awaits(*_FILTER) or not all(
            uses_sync(backend, *_FILTER) for backend in self.filter_backends
        )

    def _serializer_on_the_loop(self, source: Any, *, many: Any = False) -> Any:
        """
        The serializer of ``source``, objects already read, when building it
        runs no code of the project's and representing them cannot query: it
        reads only what they have loaded (``aio._loaded``), or the project
        says so (``REPRESENTATION_MODE = "inline"``). None otherwise, and the
        action builds it in the worker as usual.
        """
        serializer_class = self.serializer_class
        if (
            serializer_class is None
            or user_defines(self, *_SERIALIZER_FACTORY_NAMES)
            or not is_declarative_class(serializer_class)
            # ``many_init`` builds the list serializer class too.
            or (many and not is_declarative_class(_list_class(serializer_class)))
        ):
            return None
        serializer = self.get_serializer(source, many=many)
        if aiodrf_settings.REPRESENTATION_MODE == "inline" or reads_loaded(
            serializer, source
        ):
            return serializer
        return None

    def _saved_on_the_loop(self, serializer: Any) -> bool:
        """
        Whether ``serializer``, which this view built, validated and saved with
        framework code alone, represents its instance without querying.

        Not ``REPRESENTATION_MODE = "inline"``: it vouches for the project's
        querysets, and a saved instance's to-many relations are not loaded.
        """
        return (
            not user_defines(
                self, *_SERIALIZER_FACTORY_NAMES, "perform_create", "aperform_create"
            )
            and is_declarative_class(type(serializer))
            and reads_loaded(serializer, serializer.instance, fields_from_class=True)
        )

    def _mark_fields_from_class(self, serializer: Any, *performs: str) -> None:
        """
        Mark ``serializer``, which this view built, validated and saved, when
        framework code alone did so: its fields are its class's
        (``fastdrf._compiled.fields_from_class``).
        """
        # Only the compiler reads the mark.
        if (
            _uses_compiler(serializer)
            and not user_defines(self, *_SERIALIZER_FACTORY_NAMES, *performs)
            and is_declarative_class(type(serializer))
            and (
                not isinstance(serializer, serializers.ListSerializer)
                or is_declarative_class(type(serializer.child))
            )
        ):
            setattr(serializer, FIELDS_FROM_CLASS, True)

    async def _arepresent(self, serializer: Any) -> Any:
        """``aio.data`` of a serializer ``_serializer_on_the_loop`` returned."""
        data = aio.try_data(serializer)
        return await aio.data(serializer) if data is aio.NEEDS_AWAIT else data

    def _awaits_pagination(self) -> bool:
        paginator = self.pagination_class
        return self._awaits(*_PAGINATE) or (
            paginator is not None and not uses_sync(paginator, *_PAGINATE)
        )

    async def _aqueryset(self) -> tuple[Any, bool]:
        """
        Return ``(queryset, filtered)`` for the part of the queryset that
        has to be awaited; ``(None, False)`` when none of it does.
        """
        queryset = None
        if self._awaits(*_QUERYSET):
            queryset = await call_pair(self, *_QUERYSET)
        if not self._awaits_filtering():
            return queryset, False
        queryset = await run_sync(self._unfiltered_queryset)(queryset)
        return await call_pair(self, *_FILTER, queryset), True

    # -- In the worker thread ---------------------------------------------

    def _unfiltered_queryset(self, queryset: Any = None) -> Any:
        if queryset is None:
            queryset = self.get_queryset()
        return self.optimize_queryset(queryset)

    def _filtered_queryset(self, queryset: Any = None, *, filtered: Any = False) -> Any:
        if filtered:
            return queryset
        return self.filter_queryset(self._unfiltered_queryset(queryset))

    def _object(self, queryset: Any = None, *, filtered: Any = False) -> Any:
        """
        ``get_object()`` as far as it goes without awaiting. Returns
        ``(obj, checked)``: object permissions that have to be awaited are
        left to the caller.
        """
        if (
            queryset is None
            and resolve_pair(type(self), "get_object", "aget_object") is Impl.SYNC
        ):
            # Written for DRF; it checks the permissions itself.
            return self.get_object(), True

        queryset = self._filtered_queryset(queryset, filtered=filtered)

        # Perform the lookup filtering.
        lookup_url_kwarg = self.lookup_url_kwarg or self.lookup_field

        assert lookup_url_kwarg in self.kwargs, (  # noqa: S101 -- as in DRF
            "Expected view %s to be called with a URL keyword argument "
            'named "%s". Fix your URL conf, or set the `.lookup_field` '
            "attribute on the view correctly."
            % (self.__class__.__name__, lookup_url_kwarg)
        )

        filter_kwargs = {self.lookup_field: self.kwargs[lookup_url_kwarg]}
        obj = get_object_or_404(queryset, **filter_kwargs)

        # May raise a permission denied
        impl = resolve_pair(type(self), *_CHECK_OBJECT)
        if impl in (Impl.ASYNC, Impl.SYNC_IS_ASYNC):
            return obj, False
        if impl is Impl.SYNC or user_defines(self, "get_permissions"):
            self.check_object_permissions(self.request, obj)
            return obj, True
        classes = self.permission_classes
        state = self.__dict__
        if (
            type(classes) in (list, tuple)
            and "permission_denied" not in state
            and _object_permission_plan(type(self), tuple(classes))
        ):
            # DRF's ``get_permissions()`` and ``check_object_permissions()``;
            # a permission with state of its own takes the path below.
            permissions: Sequence[Any] = [permission() for permission in classes]
            if not any(vars(permission) for permission in permissions):
                for permission in permissions:
                    if not permission.has_object_permission(self.request, self, obj):
                        self.permission_denied(
                            self.request,
                            message=getattr(permission, "message", None),
                            code=getattr(permission, "code", None),
                        )
                return obj, True
        else:
            permissions = self.get_permissions()
        if policies.permissions_mode(permissions, *_OBJECT_PERMISSIONS) is Mode.ASYNC:
            return obj, False
        for permission in permissions:
            if not policies.has_object_permission(permission, self.request, self, obj):
                self.permission_denied(
                    self.request,
                    message=getattr(permission, "message", None),
                    code=getattr(permission, "code", None),
                )
        return obj, True

    @bridges_to("aget_serializer_class")
    def get_serializer_class(self) -> type[BaseSerializer]:
        """
        DRF's ``get_serializer_class``. A msgspec ``Struct`` or pydantic
        model set as ``serializer_class`` is wrapped in a serializer.
        """
        return self._serializer_class()

    def _serializer_class(self) -> Any:
        serializer_class = super().get_serializer_class()
        if isinstance(serializer_class, type) and issubclass(
            serializer_class, BaseSerializer
        ):
            return serializer_class  # checked when the URL was built
        from aiodrf.contrib.typed import adapt

        return adapt(serializer_class)

    async def aget_serializer_class(self) -> type[BaseSerializer]:
        """Async counterpart of get_serializer_class, including schema adaptation."""
        return await run_sync(self._serializer_class)()

    @bridges_to("aget_serializer_context")
    def get_serializer_context(self) -> Mapping[str, Any]:
        return super().get_serializer_context()

    async def aget_serializer_context(self) -> Mapping[str, Any]:
        """Return DRF's request, format and view context."""
        build = super().get_serializer_context
        if (
            getattr(build, "__func__", None)
            is generics.GenericAPIView.get_serializer_context
        ):
            return build()  # a dict of the request, format and view
        return await run_sync(build)()

    @bridges_to("aget_serializer")
    def get_serializer(self, *args: Any, **kwargs: Any) -> BaseSerializer:
        """
        DRF's ``get_serializer``. A class chosen by an overridden
        ``get_serializer_class`` is checked (and a bare schema wrapped) here;
        the static one was when the URL was built.
        """
        serializer_class = call_pair_sync(
            self, "get_serializer_class", "aget_serializer_class"
        )
        if user_defines(self, "get_serializer_class", "aget_serializer_class"):
            serializer_class = compile_serializer(serializer_class, type(self))
        if "context" not in kwargs:
            kwargs["context"] = call_pair_sync(
                self, "get_serializer_context", "aget_serializer_context"
            )
        return serializer_class(*args, **kwargs)

    async def aget_serializer(self, *args: Any, **kwargs: Any) -> BaseSerializer:
        """Await factory hooks, then construct the serializer in the worker."""
        serializer_class = await call_pair(
            self, "get_serializer_class", "aget_serializer_class"
        )
        if "context" not in kwargs:
            kwargs["context"] = await call_pair(
                self, "get_serializer_context", "aget_serializer_context"
            )

        def construct() -> Any:
            cls = compile_serializer(serializer_class, type(self))
            return cls(*args, **kwargs)

        return await run_sync(construct)()

    def optimize_queryset(self, queryset: QuerySet[Any]) -> QuerySet[Any]:
        """
        Prepare the queryset used by ``list`` and ``aget_object``.

        Applies ``FASTDRF["FETCH_MODE"]`` (Django 6.1+) and, when the
        serializer sets ``Meta.auto_prefetch = True``, the ``select_related``
        and ``prefetch_related`` lookups its fields need.
        """
        fetch_mode = _FETCH_MODES.get(fastdrf_settings.FETCH_MODE)
        if fetch_mode is not None:
            queryset = queryset.fetch_mode(fetch_mode)
        if self.serializer_class is None and not user_defines(
            self, "get_serializer_class", "aget_serializer_class"
        ):
            # No serializer to derive lookups from. DRF asks for one only to
            # build it, and a destroy-only view need not have one.
            return queryset
        serializer_class = call_pair_sync(
            self, "get_serializer_class", "aget_serializer_class"
        )
        if getattr(getattr(serializer_class, "Meta", None), "auto_prefetch", False):
            queryset = auto_prefetch(
                queryset,
                serializer_class,
                lambda: serializer_class(
                    context=call_pair_sync(
                        self, "get_serializer_context", "aget_serializer_context"
                    )
                ),
            )
        return queryset

    async def aget_object(self) -> ModelT:
        """Async counterpart of ``get_object``."""
        if resolve_pair(type(self), "get_object", "aget_object") is Impl.SYNC:
            # A ``get_object()`` written for DRF builds its own queryset.
            queryset, filtered = None, False
        else:
            queryset, filtered = await self._aqueryset()
        obj, checked = await run_sync(self._object)(queryset, filtered=filtered)
        if not checked:
            await call_pair(self, *_CHECK_OBJECT, self.request, obj)
        return obj

    async def afilter_queryset(self, queryset: QuerySet[ModelT]) -> QuerySet[ModelT]:
        """Async counterpart of ``filter_queryset``."""
        for backend in list(self.filter_backends):
            queryset = await policies.afilter_queryset(
                backend(), self.request, queryset, self
            )
        return queryset

    async def apaginate_queryset(
        self, queryset: QuerySet[Any] | Sequence[Any]
    ) -> list[Any] | None:
        """
        Return a single page of results, or ``None`` if pagination is disabled.

        DRF's paginators count and slice the queryset in one thread hop.
        """
        if self.paginator is None:
            return None
        return await call_pair(
            self.paginator,
            "paginate_queryset",
            "apaginate_queryset",
            queryset,
            self.request,
            view=self,
        )

    async def aget_paginated_response(self, data: Any) -> Response:
        assert self.paginator is not None  # noqa: S101 -- as in DRF
        return await call_pair(
            self.paginator, "get_paginated_response", "aget_paginated_response", data
        )


# Concrete view classes that provide method handlers
# by composing the mixin classes with the base view.


class CreateAPIView[ModelT: Model](
    mixins.CreateModelMixin, GenericAPIView[ModelT], generics.CreateAPIView
):
    # Concrete view for creating a model instance.

    async def post(
        self, request: Request, *args: Any, **kwargs: Any
    ) -> HttpResponseBase:
        return await self.acall_handler(self.create, request, *args, **kwargs)


class ListAPIView[ModelT: Model](
    mixins.ListModelMixin, GenericAPIView[ModelT], generics.ListAPIView
):
    # Concrete view for listing a queryset.

    async def get(
        self, request: Request, *args: Any, **kwargs: Any
    ) -> HttpResponseBase:
        return await self.acall_handler(self.list, request, *args, **kwargs)


class RetrieveAPIView[ModelT: Model](
    mixins.RetrieveModelMixin, GenericAPIView[ModelT], generics.RetrieveAPIView
):
    # Concrete view for retrieving a model instance.

    async def get(
        self, request: Request, *args: Any, **kwargs: Any
    ) -> HttpResponseBase:
        return await self.acall_handler(self.retrieve, request, *args, **kwargs)


class DestroyAPIView[ModelT: Model](
    mixins.DestroyModelMixin, GenericAPIView[ModelT], generics.DestroyAPIView
):
    # Concrete view for deleting a model instance.

    async def delete(
        self, request: Request, *args: Any, **kwargs: Any
    ) -> HttpResponseBase:
        return await self.acall_handler(self.destroy, request, *args, **kwargs)


class UpdateAPIView[ModelT: Model](
    mixins.UpdateModelMixin, GenericAPIView[ModelT], generics.UpdateAPIView
):
    # Concrete view for updating a model instance.

    async def put(
        self, request: Request, *args: Any, **kwargs: Any
    ) -> HttpResponseBase:
        return await self.acall_handler(self.update, request, *args, **kwargs)

    async def patch(
        self, request: Request, *args: Any, **kwargs: Any
    ) -> HttpResponseBase:
        return await self.acall_handler(self.partial_update, request, *args, **kwargs)


class ListCreateAPIView[ModelT: Model](
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    GenericAPIView[ModelT],
    generics.ListCreateAPIView,
):
    # Concrete view for listing a queryset or creating a model instance.

    async def get(
        self, request: Request, *args: Any, **kwargs: Any
    ) -> HttpResponseBase:
        return await self.acall_handler(self.list, request, *args, **kwargs)

    async def post(
        self, request: Request, *args: Any, **kwargs: Any
    ) -> HttpResponseBase:
        return await self.acall_handler(self.create, request, *args, **kwargs)


class RetrieveUpdateAPIView[ModelT: Model](
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    GenericAPIView[ModelT],
    generics.RetrieveUpdateAPIView,
):
    # Concrete view for retrieving, updating a model instance.

    async def get(
        self, request: Request, *args: Any, **kwargs: Any
    ) -> HttpResponseBase:
        return await self.acall_handler(self.retrieve, request, *args, **kwargs)

    async def put(
        self, request: Request, *args: Any, **kwargs: Any
    ) -> HttpResponseBase:
        return await self.acall_handler(self.update, request, *args, **kwargs)

    async def patch(
        self, request: Request, *args: Any, **kwargs: Any
    ) -> HttpResponseBase:
        return await self.acall_handler(self.partial_update, request, *args, **kwargs)


class RetrieveDestroyAPIView[ModelT: Model](
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    GenericAPIView[ModelT],
    generics.RetrieveDestroyAPIView,
):
    # Concrete view for retrieving or deleting a model instance.

    async def get(
        self, request: Request, *args: Any, **kwargs: Any
    ) -> HttpResponseBase:
        return await self.acall_handler(self.retrieve, request, *args, **kwargs)

    async def delete(
        self, request: Request, *args: Any, **kwargs: Any
    ) -> HttpResponseBase:
        return await self.acall_handler(self.destroy, request, *args, **kwargs)


class RetrieveUpdateDestroyAPIView[ModelT: Model](
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    GenericAPIView[ModelT],
    generics.RetrieveUpdateDestroyAPIView,
):
    # Concrete view for retrieving, updating or deleting a model instance.

    async def get(
        self, request: Request, *args: Any, **kwargs: Any
    ) -> HttpResponseBase:
        return await self.acall_handler(self.retrieve, request, *args, **kwargs)

    async def put(
        self, request: Request, *args: Any, **kwargs: Any
    ) -> HttpResponseBase:
        return await self.acall_handler(self.update, request, *args, **kwargs)

    async def patch(
        self, request: Request, *args: Any, **kwargs: Any
    ) -> HttpResponseBase:
        return await self.acall_handler(self.partial_update, request, *args, **kwargs)

    async def delete(
        self, request: Request, *args: Any, **kwargs: Any
    ) -> HttpResponseBase:
        return await self.acall_handler(self.destroy, request, *args, **kwargs)
