"""Async serializer factories on native list/retrieve paths."""

import asyncio

import pytest
from django_async_backend.db import async_new_connection

from aiodrf.test import AsyncAPIRequestFactory
from tests.async_backend.test_views import library
from tests.async_backend.urls import Books


@pytest.mark.parametrize("hook", ["class", "context", "factory"])
async def test_native_views_await_serializer_factories(hook):
    _, books = await library()
    calls = []

    class View(Books):
        pass

    if hook == "class":

        async def aget_serializer_class(self):
            calls.append(asyncio.get_running_loop())
            return await super(View, self).aget_serializer_class()

        View.aget_serializer_class = aget_serializer_class
    elif hook == "context":

        async def aget_serializer_context(self):
            calls.append(asyncio.get_running_loop())
            return await super(View, self).aget_serializer_context()

        View.aget_serializer_context = aget_serializer_context
    else:

        async def aget_serializer(self, *args, **kwargs):
            calls.append(asyncio.get_running_loop())
            return await super(View, self).aget_serializer(*args, **kwargs)

        View.aget_serializer = aget_serializer

    request = AsyncAPIRequestFactory()
    listing = async_new_connection(View.as_view({"get": "list"}))
    detail = async_new_connection(View.as_view({"get": "retrieve"}))
    assert (await listing(request.get("/"))).data["count"] == 4
    assert (await detail(request.get("/"), pk=books[0].pk)).data["id"] == books[0].pk
    assert calls
    assert all(loop is asyncio.get_running_loop() for loop in calls)
