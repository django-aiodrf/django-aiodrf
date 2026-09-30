"""
Async model mixins.

The actions keep DRF's names (``list``, ``create``, ``retrieve``, ``update``,
``partial_update``, ``destroy``) so routers and drf-spectacular treat them as
DRF actions.

Each action has a synchronous *body* that mirrors DRF's and runs in one
thread hop: ``get_queryset``, filtering, the lookup or the page,
``get_serializer``, validation, the save and the representation are
synchronous DRF code, and a project's overrides of them may query wherever
they like. Where something has to be awaited (an ``aperform_create``, async
object permissions, a serializer with async members) the body stops and
says at which :class:`Step`; the action awaits that step on the event loop
and re-enters the body after it. Nothing runs twice.

Class documentation in this module is written as comments: drf-spectacular
publishes the docstrings of view classes it does not recognise as DRF's own
as API descriptions.
"""

import enum
from typing import TYPE_CHECKING, Any

from django.http import HttpResponseBase
from rest_framework import mixins, status
from rest_framework.request import Request
from rest_framework.serializers import BaseSerializer

from aiodrf import aio
from aiodrf.aio._save import check_drf_save
from aiodrf.response import Response
from aiodrf.utils import (
    Impl,
    bridge_base,
    bridges_to,
    call_pair,
    resolve_pair,
    run_sync,
    user_defines,
)

if TYPE_CHECKING:

    class _View:
        # What the actions call on the aiodrf ``GenericAPIView`` they are
        # mixed into; declared for the type checker only.
        def get_serializer(self, *args: Any, **kwargs: Any) -> Any: ...
        def paginate_queryset(self, queryset: Any) -> Any: ...
        def get_paginated_response(self, data: Any) -> Any: ...
        def get_success_headers(self, data: Any) -> Any: ...
        def _awaits(self, sync_name: str, async_name: str) -> bool: ...
        def _awaits_serializer(self) -> bool: ...
        def _serializer_on_the_loop(
            self, source: Any, *, many: bool = False
        ) -> Any: ...
        async def _arepresent(self, serializer: Any) -> Any: ...
        def _saved_on_the_loop(self, serializer: Any) -> bool: ...
        def _mark_fields_from_class(self, serializer: Any, *performs: str) -> None: ...
        def _awaits_pagination(self) -> bool: ...
        async def _aqueryset(self) -> tuple[Any, bool]: ...
        def _filtered_queryset(
            self, queryset: Any = None, *, filtered: bool = False
        ) -> Any: ...
        def _object(
            self, queryset: Any = None, *, filtered: bool = False
        ) -> tuple[Any, bool]: ...
        async def acall_handler(
            self, handler: Any, request: Any, *args: Any, **kwargs: Any
        ) -> Any: ...

else:
    _View = object

__all__ = [
    "CreateModelMixin",
    "DestroyModelMixin",
    "ListModelMixin",
    "RetrieveModelMixin",
    "UpdateModelMixin",
]

for _base in (
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
):
    bridge_base(_base)

_CHECK_OBJECT = ("check_object_permissions", "acheck_object_permissions")
_PAGINATED_RESPONSE = ("get_paginated_response", "aget_paginated_response")
_CREATES = ("perform_create", "aperform_create")
_UPDATES = ("perform_update", "aperform_update")


class Step(enum.Enum):
    """Where a synchronous action body stopped because the step needs awaiting."""

    CHECK_OBJECT = "check_object"
    SERIALIZE = "serialize"
    VALIDATE = "validate"
    PERFORM = "perform"
    REPRESENT = "represent"
    RESPOND = "respond"


def _perform(view: Any, sync_name: str, async_name: str, target: Any) -> Any:
    """
    Run a ``perform_*`` hook in the worker. The default saves (or deletes)
    like DRF's, the save in one transaction (``ATOMIC_SAVE``).
    """
    impl = resolve_pair(view, sync_name, async_name)
    if impl in (Impl.ASYNC, Impl.SYNC_IS_ASYNC):
        return aio.NEEDS_AWAIT
    if impl is Impl.SYNC:
        # Written for DRF.
        if sync_name != "perform_destroy":
            check_drf_save(
                target, f"{type(view).__qualname__}.{sync_name}()", async_name
            )
        return getattr(view, sync_name)(target)
    if sync_name == "perform_destroy":
        return target.delete()
    return aio.try_save(target)


@bridge_base
class CreateModelMixin(mixins.CreateModelMixin, _View):
    # Create a model instance.

    async def create(
        self, request: Request, *args: Any, **kwargs: Any
    ) -> HttpResponseBase:
        step, result = await run_sync(self._create)(request)
        if step is Step.SERIALIZE:
            serializer = await call_pair(
                self, "get_serializer", "aget_serializer", data=result
            )
            step, result = await run_sync(self._create)(request, serializer)
        if step is Step.VALIDATE:
            await aio.is_valid(result, raise_exception=True)
            step, result = await run_sync(self._create)(request, result, Step.PERFORM)
        if step is Step.PERFORM:
            await call_pair(self, "perform_create", "aperform_create", result)
            self._mark_fields_from_class(result, *_CREATES)
            if self._saved_on_the_loop(result):
                # Saved by an awaited write (a native one): represented on
                # the loop when that cannot query.
                return await self._acreated(await self._arepresent(result))
            step, result = await run_sync(self._create)(request, result, Step.REPRESENT)
        if step is Step.REPRESENT:
            result = await self._acreated(await aio.data(result))
        return result

    async def _acreated(self, data: Any) -> Response:
        if user_defines(self, "get_success_headers"):
            # The default builds a header from the data; an override may query.
            return await run_sync(self._created)(data)
        return self._created(data)

    def _create(
        self, request: Request, serializer: Any = None, step: Step = Step.VALIDATE
    ) -> tuple[Step | None, Any]:
        if serializer is None:
            if self._awaits_serializer():
                return Step.SERIALIZE, request.data
            serializer = self.get_serializer(data=request.data)
        if step is Step.VALIDATE:
            if aio.try_is_valid(serializer, raise_exception=True) is aio.NEEDS_AWAIT:
                return step, serializer
            step = Step.PERFORM
        if step is Step.PERFORM:
            performed = _perform(self, "perform_create", "aperform_create", serializer)
            if performed is aio.NEEDS_AWAIT:
                return step, serializer
            self._mark_fields_from_class(serializer, *_CREATES)
        data = aio.try_data(serializer)
        if data is aio.NEEDS_AWAIT:
            return Step.REPRESENT, serializer
        return None, self._created(data)

    def _created(self, data: Any) -> Response:
        headers = self.get_success_headers(data)
        return Response(data, status=status.HTTP_201_CREATED, headers=headers)

    @bridges_to("aperform_create")
    def perform_create(self, serializer: BaseSerializer) -> None:
        # Sync callers must not skip an ``aperform_create`` without a twin.
        return super().perform_create(serializer)  # type: ignore[misc]

    async def aperform_create(self, serializer: BaseSerializer) -> None:
        await aio.save(serializer)


@bridge_base
class ListModelMixin(mixins.ListModelMixin, _View):
    # List a queryset.

    async def list(
        self, request: Request, *args: Any, **kwargs: Any
    ) -> HttpResponseBase:
        queryset, filtered = await self._aqueryset()
        if self._awaits_pagination():
            queryset = await run_sync(self._filtered_queryset)(
                queryset, filtered=filtered
            )
            page = await call_pair(
                self, "paginate_queryset", "apaginate_queryset", queryset
            )
            objects, paginated = (queryset, False) if page is None else (page, True)
            step, result = await run_sync(self._list_page)(objects, paginated)
        else:
            step, result, paginated = await run_sync(self._list)(
                queryset, filtered=filtered
            )

        if step is Step.SERIALIZE:
            serializer = await call_pair(
                self, "get_serializer", "aget_serializer", result, many=True
            )
            step, result = Step.REPRESENT, serializer
        if step is Step.REPRESENT:
            step, result = Step.RESPOND, await aio.data(result)
        if step is Step.RESPOND:
            if not paginated:
                return Response(result)
            return await call_pair(self, *_PAGINATED_RESPONSE, result)
        return result

    def _list(self, queryset: Any = None, *, filtered: bool = False) -> Any:
        queryset = self._filtered_queryset(queryset, filtered=filtered)
        page = self.paginate_queryset(queryset)
        objects, paginated = (queryset, False) if page is None else (page, True)
        return (*self._list_page(objects, paginated), paginated)

    def _list_page(self, objects: Any, paginated: bool) -> Any:
        if self._awaits_serializer():
            return Step.SERIALIZE, objects
        serializer = self.get_serializer(objects, many=True)
        data = aio.try_data(serializer)
        if data is aio.NEEDS_AWAIT:
            return Step.REPRESENT, serializer
        if not paginated:
            return None, Response(data)
        if self._awaits(*_PAGINATED_RESPONSE):
            return Step.RESPOND, data
        return None, self.get_paginated_response(data)


class _ObjectMixin:
    if TYPE_CHECKING:

        async def _aqueryset(self) -> tuple[Any, bool]: ...

    # The start of the actions that work on one object.

    async def _aobject(self) -> tuple[Any, bool, Any]:
        """
        Return ``(queryset, filtered, instance)``: the instance when getting
        it has to be awaited as a whole, else what of its queryset has.
        """
        impl = resolve_pair(self, "get_object", "aget_object")
        if impl in (Impl.ASYNC, Impl.SYNC_IS_ASYNC):
            return None, False, await call_pair(self, "get_object", "aget_object")
        if impl is Impl.SYNC:
            # A ``get_object()`` written for DRF builds its own queryset.
            return None, False, None
        return (*await self._aqueryset(), None)


@bridge_base
class RetrieveModelMixin(_ObjectMixin, mixins.RetrieveModelMixin, _View):
    # Retrieve a model instance.

    async def retrieve(
        self, request: Request, *args: Any, **kwargs: Any
    ) -> HttpResponseBase:
        queryset, filtered, instance = await self._aobject()
        # Read by an awaited get_object (a native queryset's): represented on
        # the loop when that cannot query.
        if instance is not None and (
            serializer := self._serializer_on_the_loop(instance)
        ):
            return Response(await self._arepresent(serializer))
        body = run_sync(self._retrieve)
        step, result = await body(queryset, filtered=filtered, instance=instance)
        if step is Step.CHECK_OBJECT:
            await call_pair(self, *_CHECK_OBJECT, request, result)
            step, result = await body(instance=result)
        if step is Step.SERIALIZE:
            serializer = await call_pair(
                self, "get_serializer", "aget_serializer", result
            )
            step, result = Step.REPRESENT, serializer
        if step is Step.REPRESENT:
            result = Response(await aio.data(result))
        return result

    def _retrieve(
        self, queryset: Any = None, *, filtered: bool = False, instance: Any = None
    ) -> Any:
        if instance is None:
            instance, checked = self._object(queryset, filtered=filtered)
            if not checked:
                return Step.CHECK_OBJECT, instance
        if self._awaits_serializer():
            return Step.SERIALIZE, instance
        serializer = self.get_serializer(instance)
        data = aio.try_data(serializer)
        if data is aio.NEEDS_AWAIT:
            return Step.REPRESENT, serializer
        return None, Response(data)


@bridge_base
class UpdateModelMixin(_ObjectMixin, mixins.UpdateModelMixin, _View):
    # Update a model instance.

    async def update(
        self, request: Request, *args: Any, **kwargs: Any
    ) -> HttpResponseBase:
        partial = kwargs.pop("partial", False)
        queryset, filtered, instance = await self._aobject()
        body = run_sync(self._update)
        step, result = await body(
            request, partial, queryset, filtered=filtered, instance=instance
        )
        if step is Step.CHECK_OBJECT:
            await call_pair(self, *_CHECK_OBJECT, request, result)
            step, result = await body(request, partial, instance=result)
        if step is Step.SERIALIZE:
            instance, data = result
            serializer = await call_pair(
                self,
                "get_serializer",
                "aget_serializer",
                instance,
                data=data,
                partial=partial,
            )
            step, result = await body(request, partial, serializer=serializer)
        if step is Step.VALIDATE:
            await aio.is_valid(result, raise_exception=True)
            step, result = await body(
                request, partial, serializer=result, step=Step.PERFORM
            )
        if step is Step.PERFORM:
            await call_pair(self, "perform_update", "aperform_update", result)
            self._mark_fields_from_class(result, *_UPDATES)
            step, result = await body(
                request, partial, serializer=result, step=Step.REPRESENT
            )
        if step is Step.REPRESENT:
            result = Response(await aio.data(result))
        return result

    def _update(
        self,
        request: Request,
        partial: bool,
        queryset: Any = None,
        *,
        filtered: bool = False,
        instance: Any = None,
        serializer: Any = None,
        step: Step = Step.VALIDATE,
    ) -> tuple[Step | None, Any]:
        if serializer is None:
            if instance is None:
                instance, checked = self._object(queryset, filtered=filtered)
                if not checked:
                    return Step.CHECK_OBJECT, instance
            if self._awaits_serializer():
                return Step.SERIALIZE, (instance, request.data)
            serializer = self.get_serializer(
                instance, data=request.data, partial=partial
            )
        if step is Step.VALIDATE:
            if aio.try_is_valid(serializer, raise_exception=True) is aio.NEEDS_AWAIT:
                return step, serializer
            step = Step.PERFORM
        if step is Step.PERFORM:
            performed = _perform(self, "perform_update", "aperform_update", serializer)
            if performed is aio.NEEDS_AWAIT:
                return step, serializer
            self._mark_fields_from_class(serializer, *_UPDATES)

        if getattr(serializer.instance, "_prefetched_objects_cache", None):
            # If 'prefetch_related' has been applied to a queryset, we need to
            # forcibly invalidate the prefetch cache on the instance.
            serializer.instance._prefetched_objects_cache = {}

        data = aio.try_data(serializer)
        if data is aio.NEEDS_AWAIT:
            return Step.REPRESENT, serializer
        return None, Response(data)

    @bridges_to("aperform_update")
    def perform_update(self, serializer: BaseSerializer) -> None:
        return super().perform_update(serializer)  # type: ignore[misc]

    async def aperform_update(self, serializer: BaseSerializer) -> None:
        await aio.save(serializer)

    async def partial_update(
        self, request: Request, *args: Any, **kwargs: Any
    ) -> HttpResponseBase:
        kwargs["partial"] = True
        return await self.acall_handler(self.update, request, *args, **kwargs)


@bridge_base
class DestroyModelMixin(_ObjectMixin, mixins.DestroyModelMixin, _View):
    # Destroy a model instance.

    async def destroy(
        self, request: Request, *args: Any, **kwargs: Any
    ) -> HttpResponseBase:
        queryset, filtered, instance = await self._aobject()
        body = run_sync(self._destroy)
        step, result = await body(queryset, filtered=filtered, instance=instance)
        if step is Step.CHECK_OBJECT:
            await call_pair(self, *_CHECK_OBJECT, request, result)
            step, result = await body(instance=result)
        if step is Step.PERFORM:
            await call_pair(self, "perform_destroy", "aperform_destroy", result)
            result = Response(status=status.HTTP_204_NO_CONTENT)
        return result

    def _destroy(
        self, queryset: Any = None, *, filtered: bool = False, instance: Any = None
    ) -> Any:
        if instance is None:
            instance, checked = self._object(queryset, filtered=filtered)
            if not checked:
                return Step.CHECK_OBJECT, instance
        if (
            _perform(self, "perform_destroy", "aperform_destroy", instance)
            is aio.NEEDS_AWAIT
        ):
            return Step.PERFORM, instance
        return None, Response(status=status.HTTP_204_NO_CONTENT)

    @bridges_to("aperform_destroy")
    def perform_destroy(self, instance: Any) -> None:
        return super().perform_destroy(instance)  # type: ignore[misc]

    async def aperform_destroy(self, instance: Any) -> None:
        await run_sync(instance.delete)()
