"""
``cache_page`` for async views.

Django's ``cache_page`` looks the response up with a synchronous
``cache.get()`` before it awaits the view
(``django.utils.decorators.make_middleware_decorator``). On the event loop
that blocks every other request for a round trip to Redis or Memcached, and
with the database cache it raises ``SynchronousOnlyOperation``. The decorator
here does the lookup in a thread unless the cache lives in the process::

    from aiodrf.cache import cache_page

    class BookViewSet(viewsets.ReadOnlyModelViewSet):
        @method_decorator(cache_page(60))
        async def list(self, request, *args, **kwargs):
            return await super().list(request, *args, **kwargs)

Everything else is Django's ``CacheMiddleware``: the keys, ``Vary``,
``Cache-Control`` and what is cacheable are the same, and a synchronous view
gets Django's decorator unchanged. ``vary_on_headers``, ``vary_on_cookie``,
``cache_control`` and ``never_cache`` do no I/O; use Django's.
"""

from collections.abc import Callable, Sequence
from functools import wraps
from inspect import iscoroutinefunction
from typing import Any

from django.core.cache.backends.base import BaseCache
from django.http import HttpRequest
from django.middleware.cache import CacheMiddleware
from django.views.decorators import cache as django_cache

import aiodrf._builtins  # noqa: F401 -- registers Django's in-process caches
from aiodrf.compat import resolve_cache
from aiodrf.utils import is_pure, run_sync

__all__ = ["cache_page", "is_in_process_cache"]


def is_in_process_cache(
    cache: BaseCache, methods: Sequence[str] = ("get", "set")
) -> bool:
    """
    True if ``methods`` of ``cache`` do no I/O and may be used on the event loop.

    Django's ``LocMemCache`` and ``DummyCache`` are registered pure. A
    subclass that adds code (a second tier behind the local one, say) is
    not, unless it declares itself with ``async_safe = True``.
    """
    backend = resolve_cache(cache)
    return all(is_pure(backend, name) for name in methods)


def cache_page(
    timeout: int, *, cache: str | None = None, key_prefix: str | None = None
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorator(view_func: Callable[..., Any]) -> Callable[..., Any]:
        if not iscoroutinefunction(view_func):
            return django_cache.cache_page(timeout, cache=cache, key_prefix=key_prefix)(
                view_func
            )

        middleware = CacheMiddleware(
            view_func, page_timeout=timeout, cache_alias=cache, key_prefix=key_prefix
        )

        async def call(method: Callable[..., Any], *args: Any) -> Any:
            if is_in_process_cache(middleware.cache):
                return method(*args)
            return await run_sync(method)(*args)

        @wraps(view_func)
        async def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> Any:
            response = await call(middleware.process_request, request)
            if response is not None:
                return response
            response = await view_func(request, *args, **kwargs)
            if (
                hasattr(response, "render")
                and callable(response.render)
                and not getattr(response, "is_rendered", False)
            ):
                # As Django does: store the response once it is rendered.
                # Django renders in a thread when there are callbacks. A
                # rendered response would run the callback right here, on
                # the loop: it is stored below instead.
                response.add_post_render_callback(
                    lambda rendered: middleware.process_response(request, rendered)
                )
                return response
            return await call(middleware.process_response, request, response)

        return wrapper

    return decorator
