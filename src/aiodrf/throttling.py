"""Async throttle hooks and cache-backed fixed-window rate limits."""

import math
from typing import TYPE_CHECKING

from rest_framework import throttling
from rest_framework.request import Request
from rest_framework.throttling import (
    AnonRateThrottle,
    ScopedRateThrottle,
    SimpleRateThrottle,
    UserRateThrottle,
)

from aiodrf.utils import bridge_base, bridges_to

if TYPE_CHECKING:
    # Not at run time: ``rest_framework.views`` imports this module while its
    # ``APIView`` class body resolves the ``DEFAULT_*_CLASSES`` settings.
    from rest_framework.views import APIView

__all__ = [
    "AnonFixedWindowRateThrottle",
    "AnonRateThrottle",
    "BaseThrottle",
    "FixedWindowRateThrottle",
    "ScopedFixedWindowRateThrottle",
    "ScopedRateThrottle",
    "SimpleRateThrottle",
    "UserFixedWindowRateThrottle",
    "UserRateThrottle",
]

bridge_base(throttling.BaseThrottle)

# DRF's rate throttles need no async variants: their only I/O is one cache
# read and one cache write, and Django's async cache API would spend a thread
# hop on each. aiodrf evaluates them inline for in-process caches and in a
# single hop otherwise (see ``aiodrf.policies``).


@bridge_base
class BaseThrottle(throttling.BaseThrottle):
    """Base class for throttles implemented with ``async def aallow_request``."""

    @bridges_to("aallow_request")
    def allow_request(self, request: Request, view: "APIView") -> bool:
        return super().allow_request(request, view)

    async def aallow_request(self, request: Request, view: "APIView") -> bool:
        raise NotImplementedError(".aallow_request() must be overridden")


class FixedWindowRateThrottle(SimpleRateThrottle):
    """
    ``SimpleRateThrottle`` counted in fixed windows with the cache's atomic
    ``add`` and ``incr``.

    DRF's throttles read the request history, append to it and write it
    back: concurrent requests read the same history and all pass, as DRF's
    documentation warns. Here each request increments one counter per
    window (``<cache key>:<window number>``), which LocMem, Memcached and
    Redis do atomically, so at most ``num_requests`` requests pass per window
    and process. The database and file caches increment with a read and a
    write, so the limit is approximate under concurrency there, as with DRF.

    Rates, scopes and cache keys are DRF's. The window starts at a multiple of
    the duration, not at the first request, so a client can send the rate at
    the end of one window and again at the start of the next.
    """

    # Set by DRF's ``SimpleRateThrottle.__init__``; its stubs omit them.
    num_requests: int
    duration: int

    def allow_request(self, request: Request, view: "APIView") -> bool:
        if self.rate is None:
            return True
        self.key = self.get_cache_key(request, view)
        if self.key is None:
            return True
        self.now = self.timer()
        window = int(self.now // self.duration)
        self.window_end = (window + 1) * self.duration
        key = f"{self.key}:{window}"
        # One second beyond the window, so the counter cannot expire while
        # requests of its window still increment it.
        timeout = math.ceil(self.window_end - self.now) + 1
        count = self._count(key, timeout)
        if count > self.num_requests:
            return self.throttle_failure()
        return True

    def wait(self) -> float:
        return self.window_end - self.now

    # Tries of ``add`` then ``incr`` against a counter evicted in between.
    _COUNT_ATTEMPTS = 3

    def _count(self, key: str, timeout: int) -> int:
        """This request's number in the window: 1 if it created the counter."""
        for _ in range(self._COUNT_ATTEMPTS):
            if self.cache.add(key, 1, timeout):
                return 1
            try:
                return self.cache.incr(key)
            except ValueError:
                # Evicted between ``add`` and ``incr``; another request may
                # create the counter first, so both are tried again.
                continue
        # A cache that keeps evicting the counter cannot count the window.
        return 1


class AnonFixedWindowRateThrottle(FixedWindowRateThrottle, AnonRateThrottle):
    """DRF's ``AnonRateThrottle`` (scope ``"anon"``), counted in fixed windows."""


class UserFixedWindowRateThrottle(FixedWindowRateThrottle, UserRateThrottle):
    """DRF's ``UserRateThrottle`` (scope ``"user"``), counted in fixed windows."""


class ScopedFixedWindowRateThrottle(ScopedRateThrottle, FixedWindowRateThrottle):
    """DRF's ``ScopedRateThrottle`` (the view's ``throttle_scope``), counted in fixed windows."""
