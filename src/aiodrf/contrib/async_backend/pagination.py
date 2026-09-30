"""
DRF's paginators for native querysets: the count and the rows are awaited.

Each ``apaginate_queryset`` follows DRF's ``paginate_queryset`` step by
step (and, for pages, Django's ``Paginator.page``), so query parameters,
error messages, ``self.page`` and the paginated response are DRF's. Only
the two evaluations differ: ``await queryset.acount()`` and the rows read
with ``async for``. ``tests/async_backend/test_mirrors.py`` pins the DRF
and Django code they follow.
"""

import inspect
from typing import Any

from django.core.paginator import InvalidPage, Paginator
from rest_framework import pagination
from rest_framework.exceptions import NotFound
from rest_framework.pagination import _reverse_ordering

from aiodrf.contrib.async_backend._package import run_native
from aiodrf.utils import is_async_callable, run_sync, user_defines

__all__ = [
    "CursorPagination",
    "LimitOffsetPagination",
    "NativePagination",
    "PageNumberPagination",
]


class NativePagination:
    """Base of the paginators that evaluate native querysets."""

    def paginate_queryset(self, queryset: Any, request: Any, view: Any = None) -> Any:
        # For synchronous callers, on a connection of their own.
        return run_native(self.apaginate_queryset, queryset, request, view)

    async def apaginate_queryset(
        self, queryset: Any, request: Any, view: Any = None
    ) -> Any:
        raise NotImplementedError("apaginate_queryset() must be implemented.")

    async def _hook(self, name: str, *args: Any) -> Any:
        # DRF's hooks read the query string; an override is the project's
        # code, which may query.
        method = getattr(self, name)
        if not user_defines(self, name):
            return method(*args)
        if is_async_callable(method):
            return await method(*args)
        return await run_sync(method)(*args)


#: Django's ``Paginator.count``, which counts with ``object_list.count()``.
_DJANGO_COUNT = inspect.getattr_static(Paginator, "count")


class PageNumberPagination(NativePagination, pagination.PageNumberPagination):
    async def apaginate_queryset(
        self, queryset: Any, request: Any, view: Any = None
    ) -> Any:
        self.request = request
        page_size = await self._hook("get_page_size", request)
        if not page_size:
            return None

        paginator = self.django_paginator_class(queryset, page_size)
        if inspect.getattr_static(type(paginator), "count") is _DJANGO_COUNT:
            # A cached property: counted here, it is read by ``num_pages``,
            # ``validate_number`` and the response links.
            paginator.__dict__["count"] = await queryset.acount()
        else:
            # The project's count (capped or estimated, say) may query.
            await run_sync(getattr)(paginator, "count")
        page_number = await self._hook("get_page_number", request, paginator)

        try:
            # Django's ``Paginator.page``, with the rows awaited.
            number = paginator.validate_number(page_number)
            bottom = (number - 1) * paginator.per_page
            top = bottom + paginator.per_page
            if top + paginator.orphans >= paginator.count:
                top = paginator.count
            rows = [row async for row in queryset[bottom:top]]
            self.page = paginator._get_page(rows, number, paginator)  # type: ignore[attr-defined]
        except InvalidPage as exc:
            msg = self.invalid_page_message.format(
                page_number=page_number, message=str(exc)
            )
            raise NotFound(msg) from None

        if paginator.num_pages > 1 and self.template is not None:
            # The browsable API should display pagination controls.
            self.display_page_controls = True

        return list(self.page)


class LimitOffsetPagination(NativePagination, pagination.LimitOffsetPagination):
    async def apaginate_queryset(
        self, queryset: Any, request: Any, view: Any = None
    ) -> Any:
        self.request = request
        self.limit = await self._hook("get_limit", request)
        if self.limit is None:
            return None

        # DRF counts with ``get_count()``; an override may cap or estimate.
        if user_defines(self, "get_count"):
            self.count = await self._hook("get_count", queryset)
        else:
            self.count = await queryset.acount()
        self.offset = await self._hook("get_offset", request)
        if self.count > self.limit and self.template is not None:
            self.display_page_controls = True

        if self.count == 0 or self.offset > self.count:
            return []
        return [row async for row in queryset[self.offset : self.offset + self.limit]]


class CursorPagination(NativePagination, pagination.CursorPagination):
    async def apaginate_queryset(
        self, queryset: Any, request: Any, view: Any = None
    ) -> Any:
        self.request = request
        self.page_size = await self._hook("get_page_size", request)
        if not self.page_size:
            return None

        self.base_url = request.build_absolute_uri()
        self.ordering = await self._hook("get_ordering", request, queryset, view)

        self.cursor = await self._hook("decode_cursor", request)
        if self.cursor is None:
            (offset, reverse, current_position) = (0, False, None)
        else:
            (offset, reverse, current_position) = self.cursor

        # Cursor pagination always enforces an ordering.
        if reverse:
            queryset = queryset.order_by(*_reverse_ordering(self.ordering))
        else:
            queryset = queryset.order_by(*self.ordering)

        # If we have a cursor with a fixed position then filter by that.
        if current_position is not None:
            order = self.ordering[0]
            is_reversed = order.startswith("-")
            order_attr = order.lstrip("-")

            # Test for: (cursor reversed) XOR (queryset reversed)
            if self.cursor.reverse != is_reversed:
                kwargs = {order_attr + "__lt": current_position}
            else:
                kwargs = {order_attr + "__gt": current_position}

            queryset = queryset.filter(**kwargs)

        # The only evaluation: one extra row tells whether a page follows.
        results = [row async for row in queryset[offset : offset + self.page_size + 1]]
        self.page = list(results[: self.page_size])

        # Determine the position of the final item following the page.
        if len(results) > len(self.page):
            has_following_position = True
            following_position = self._get_position_from_instance(
                results[-1], self.ordering
            )
        else:
            has_following_position = False
            following_position = None

        if reverse:
            # If we have a reverse queryset, then the query ordering was in reverse
            # so we need to reverse the items again before returning them to the user.
            self.page = list(reversed(self.page))

            # Determine next and previous positions for reverse cursors.
            self.has_next = (current_position is not None) or (offset > 0)
            self.has_previous = has_following_position
            if self.has_next:
                self.next_position = current_position  # type: ignore[assignment]
            if self.has_previous:
                self.previous_position = following_position
        else:
            # Determine next and previous positions for forward cursors.
            self.has_next = has_following_position
            self.has_previous = (current_position is not None) or (offset > 0)
            if self.has_next:
                self.next_position = following_position
            if self.has_previous:
                self.previous_position = current_position  # type: ignore[assignment]

        # Display page controls in the browsable API if there is more
        # than one page.
        if (self.has_previous or self.has_next) and self.template is not None:
            self.display_page_controls = True

        return self.page
