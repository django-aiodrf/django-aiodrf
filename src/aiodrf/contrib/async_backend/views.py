"""
aiodrf's generic views and viewsets for native querysets.

A view's queryset is the model's native one (``Model.async_objects``); lists,
pages and single objects are read with it on the event loop, in the
request's task, which owns the native connection. What DRF does
synchronously stays DRF's: filter backends compose the queryset,
serializers validate and represent, in aiodrf's worker thread. Lazy
relations read there go through Django's connection.

``select_related`` lookups (the view's queryset, ``Meta.auto_prefetch``)
are applied to the native queryset. The native queryset has no
``prefetch_related``: the prefetch lookups of ``Meta.auto_prefetch`` and of
the view's ``prefetch_related`` are loaded on the rows once they are read,
with Django's ``prefetch_related_objects``: for a list in the hop that
represents the rows, for a single object before its permissions are checked.
"""

from collections.abc import AsyncIterator, Sequence
from typing import TYPE_CHECKING, Any, cast

from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.handlers.wsgi import WSGIRequest
from django.db.models import (
    Prefetch,
    aprefetch_related_objects,
    prefetch_related_objects,
)
from django.http import Http404, HttpResponseBase, StreamingHttpResponse
from fastdrf.prefetch import _lookups_for
from fastdrf.settings import fastdrf_settings
from rest_framework.request import Request
from rest_framework.response import Response

from aiodrf import aio, generics, mixins, viewsets
from aiodrf.contrib.async_backend._package import (
    NativeQuerySet,
    async_new_connection,
    awrite_alias,
    receivers_in_transaction,
    require_installed,
    run_native,
)
from aiodrf.contrib.async_backend.pagination import NativePagination
from aiodrf.generics import _CHECK_OBJECT, _FETCH_MODES, _QUERYSET
from aiodrf.mixins import _PAGINATED_RESPONSE, Step
from aiodrf.utils import call_pair, call_pair_sync, is_pure, run_sync, user_defines

_SERIALIZER_FACTORY = (
    "get_serializer",
    "get_serializer_class",
    "get_serializer_context",
)

__all__ = [
    "CreateAPIView",
    "DestroyAPIView",
    "GenericAPIView",
    "GenericViewSet",
    "ListAPIView",
    "ListCreateAPIView",
    "ModelViewSet",
    "NativeViewMixin",
    "ReadOnlyModelViewSet",
    "RetrieveAPIView",
    "RetrieveDestroyAPIView",
    "RetrieveUpdateAPIView",
    "RetrieveUpdateDestroyAPIView",
    "UpdateAPIView",
]


if TYPE_CHECKING:
    # The aiodrf generic view the mixin is mixed into, before it.
    _View = generics.GenericAPIView
else:
    _View = object


class NativeViewMixin(_View):
    # Mixed into an aiodrf generic view or viewset, before it.

    #: Lookups loaded on the rows after they are read, as ``prefetch_related``.
    prefetch_related: Sequence[str | Prefetch] = ()

    @classmethod
    def _compile_serializers(cls, initkwargs: dict[str, Any]) -> None:
        super()._compile_serializers(initkwargs)
        require_installed(cls)
        paginator = initkwargs.get("pagination_class", cls.pagination_class)
        if paginator is not None and not issubclass(paginator, NativePagination):
            raise ImproperlyConfigured(
                f"{cls.__module__}.{cls.__qualname__}.pagination_class is "
                f"{paginator.__qualname__}, which counts and slices the queryset "
                "synchronously. Use PageNumberPagination, LimitOffsetPagination or "
                "CursorPagination from aiodrf.contrib.async_backend.pagination."
            )

    async def dispatch(
        self, request: Any, *args: Any, **kwargs: Any
    ) -> HttpResponseBase:
        if not isinstance(request, WSGIRequest):
            return await super().dispatch(request, *args, **kwargs)
        # Under WSGI each request runs in an event loop of its own
        # (``async_to_sync``), which the connection must not outlive, and
        # asgiref carries the request's context, with its connection, back
        # to the thread: a connection scope of its own closes it on return.
        async with async_new_connection():
            response = await super().dispatch(request, *args, **kwargs)
        if isinstance(response, StreamingHttpResponse) and response.is_async:
            # Django collects an async body in another event loop, after
            # this returns: the body gets a connection scope of its own.
            response.streaming_content = _on_a_connection_of_its_own(
                response.streaming_content
            )
        return response

    def get_queryset(self) -> Any:
        # DRF's clones Django querysets only; a native queryset on the class
        # would otherwise keep the first rows read.
        queryset = super().get_queryset()
        return queryset.all() if isinstance(queryset, NativeQuerySet) else queryset

    async def aget_queryset(self) -> Any:
        queryset = await super().aget_queryset()
        return queryset.all() if isinstance(queryset, NativeQuerySet) else queryset

    def optimize_queryset(self, queryset: Any) -> Any:
        # aiodrf's, split: select_related now, prefetch lookups after the read.
        fetch_mode = _FETCH_MODES.get(fastdrf_settings.FETCH_MODE)
        if fetch_mode is not None:
            queryset = queryset.fetch_mode(fetch_mode)
        self._prefetch = list(self.prefetch_related)
        if self.serializer_class is None and not user_defines(
            self, "get_serializer_class", "aget_serializer_class"
        ):
            return queryset
        serializer_class = call_pair_sync(
            self, "get_serializer_class", "aget_serializer_class"
        )
        if getattr(getattr(serializer_class, "Meta", None), "auto_prefetch", False):
            select, prefetch = _lookups_for(
                serializer_class,
                queryset.model,
                lambda: serializer_class(
                    context=call_pair_sync(
                        self, "get_serializer_context", "aget_serializer_context"
                    )
                ),
            )
            if select:
                queryset = queryset.select_related(*select)
            self._prefetch.extend(prefetch)
        return queryset

    async def _aprefetch(self, rows: list[Any]) -> None:
        if rows and self._prefetch:
            await aprefetch_related_objects(rows, *self._prefetch)

    async def _aqueryset_for_reading(self) -> Any:
        # The view's own lookups, whether or not an optimize_queryset()
        # override calls super(), which adds those of Meta.auto_prefetch.
        self._prefetch = list(self.prefetch_related)
        # A synchronous get_queryset() override runs in a hop, as it may query.
        queryset = await call_pair(self, *_QUERYSET)
        if not isinstance(queryset, NativeQuerySet):
            raise ImproperlyConfigured(
                f"{type(self).__qualname__} needs a native queryset "
                "(Model.async_objects), not "
                f"{type(queryset).__module__}.{type(queryset).__qualname__}."
            )
        if self._awaits_filtering():
            if self._optimizes_inline():
                queryset = self.optimize_queryset(queryset)
            else:
                queryset = await run_sync(self.optimize_queryset)(queryset)
            # The view's filter_queryset() override, if it has one, wraps
            # the backends: a synchronous one runs in a worker.
            return await call_pair(
                self, "filter_queryset", "afilter_queryset", queryset
            )
        if self._optimizes_inline() and self._filters_inline():
            return self.filter_queryset(self.optimize_queryset(queryset))
        # The project's hooks and synchronous filter backends compose the
        # queryset, and may query (django-filter validates a ModelChoiceFilter
        # with one): one hop for both.
        return await run_sync(self._compose)(queryset)

    def _compose(self, queryset: Any) -> Any:
        return self.filter_queryset(self.optimize_queryset(queryset))

    def _optimizes_inline(self) -> bool:
        # Meta.auto_prefetch builds the serializer: its __init__ and
        # get_fields() are the project's code, like the factory hooks.
        if self._awaits_serializer() or user_defines(
            self, "optimize_queryset", *_SERIALIZER_FACTORY
        ):
            return False
        return not getattr(
            getattr(self.serializer_class, "Meta", None), "auto_prefetch", False
        )

    def _filters_inline(self) -> bool:
        if user_defines(self, "filter_queryset"):
            return False
        return all(
            is_pure(backend, "filter_queryset") for backend in self.filter_backends
        )

    # The list action. The type checker compares it with the stubs'
    # synchronous one in the classes below ([misc] there).
    async def list(
        self, request: Request, *args: Any, **kwargs: Any
    ) -> HttpResponseBase:
        queryset = await self._aqueryset_for_reading()
        page = await self.apaginate_queryset(queryset)
        paginated = page is not None
        rows = page if paginated else [row async for row in queryset]
        # Rows with nothing left to load, represented on the loop when that
        # cannot query.
        if not self._prefetch and (
            serializer := self._serializer_on_the_loop(rows, many=True)
        ):
            data = await self._arepresent(serializer)
            if not paginated:
                return Response(data)
            return await call_pair(self, *_PAGINATED_RESPONSE, data)
        # The rows are read; the rest is aiodrf's list: the serializer is built
        # and represents the rows in the worker, as the project's code may query.
        # The prefetch, synchronous in Django too, goes with it: one hop.
        step, result = await run_sync(self._prefetched_list_page)(rows, paginated)
        if step is Step.SERIALIZE:
            step, result = (
                Step.REPRESENT,
                await call_pair(
                    self, "get_serializer", "aget_serializer", result, many=True
                ),
            )
        if step is Step.REPRESENT:
            step, result = Step.RESPOND, await aio.data(result)
        if step is Step.RESPOND:
            if not paginated:
                return Response(result)
            return await call_pair(self, *_PAGINATED_RESPONSE, result)
        return result

    def _prefetched_list_page(self, rows: Any, paginated: bool) -> Any:
        if rows and self._prefetch:
            prefetch_related_objects(rows, *self._prefetch)
        return mixins.ListModelMixin._list_page(cast(Any, self), rows, paginated)

    async def aget_object(self) -> Any:
        """DRF's ``get_object`` with the native queryset's ``aget``."""
        queryset = await self._aqueryset_for_reading()

        # Perform the lookup filtering.
        lookup_url_kwarg = self.lookup_url_kwarg or self.lookup_field

        assert lookup_url_kwarg in self.kwargs, (  # noqa: S101 -- as in DRF
            "Expected view %s to be called with a URL keyword argument "
            'named "%s". Fix your URL conf, or set the `.lookup_field` '
            "attribute on the view correctly."
            % (self.__class__.__name__, lookup_url_kwarg)
        )

        filter_kwargs = {self.lookup_field: self.kwargs[lookup_url_kwarg]}
        try:
            obj = await queryset.aget(**filter_kwargs)
        except queryset.model.DoesNotExist:
            raise Http404(
                f"No {queryset.model._meta.object_name} matches the given query."
            ) from None
        except (TypeError, ValueError, ValidationError):
            # DRF's get_object_or_404: a malformed lookup value is a 404.
            raise Http404 from None
        await self._aprefetch([obj])

        # May raise a permission denied
        await call_pair(self, *_CHECK_OBJECT, self.request, obj)
        return obj

    def get_object(self) -> Any:
        # For synchronous callers (the browsable API), on a connection of their own.
        return run_native(self.aget_object)

    async def aperform_destroy(self, instance: Any) -> None:
        await _adelete(instance)

    def perform_destroy(self, instance: Any) -> None:
        run_native(_adelete, instance)


async def _adelete(instance: Any) -> None:
    # The deletion sends its signals in a native transaction of its own, on
    # the alias Model.delete() routes to.
    using = await awrite_alias(type(instance), instance=instance)
    async with receivers_in_transaction(using):
        await instance.async_delete(using=using)


async def _on_a_connection_of_its_own(
    content: Any,
) -> AsyncIterator[Any]:
    async with async_new_connection():
        async for chunk in content:
            yield chunk


class GenericAPIView(NativeViewMixin, generics.GenericAPIView):
    pass


class CreateAPIView(NativeViewMixin, generics.CreateAPIView):
    pass


class ListAPIView(NativeViewMixin, generics.ListAPIView):  # type: ignore[misc]
    pass


class RetrieveAPIView(NativeViewMixin, generics.RetrieveAPIView):
    pass


class DestroyAPIView(NativeViewMixin, generics.DestroyAPIView):
    pass


class UpdateAPIView(NativeViewMixin, generics.UpdateAPIView):
    pass


class ListCreateAPIView(NativeViewMixin, generics.ListCreateAPIView):  # type: ignore[misc]
    pass


class RetrieveUpdateAPIView(NativeViewMixin, generics.RetrieveUpdateAPIView):
    pass


class RetrieveDestroyAPIView(NativeViewMixin, generics.RetrieveDestroyAPIView):
    pass


class RetrieveUpdateDestroyAPIView(
    NativeViewMixin, generics.RetrieveUpdateDestroyAPIView
):
    pass


class GenericViewSet(NativeViewMixin, viewsets.GenericViewSet):
    pass


class ReadOnlyModelViewSet(NativeViewMixin, viewsets.ReadOnlyModelViewSet):  # type: ignore[misc]
    pass


class ModelViewSet(NativeViewMixin, viewsets.ModelViewSet):  # type: ignore[misc]
    pass
