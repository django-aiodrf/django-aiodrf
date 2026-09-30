"""Hook lookup preserves descriptors, instance overrides and execution boundaries."""

import functools
import threading

import pytest

from aiodrf.test import count_hops
from aiodrf.utils import async_safe, bridge_base, call_pair, user_defines


async def test_a_default_async_descriptor_is_bound_once():
    reads = []

    @bridge_base
    class Base:
        def perform(self):
            raise AssertionError("Async default was not selected")

        @property
        def aperform(self):
            reads.append(True)

            async def run():
                return len(reads)

            return run

    assert await call_pair(Base(), "perform", "aperform") == 1
    assert reads == [True]


async def test_a_default_without_an_async_member_uses_the_worker():
    @bridge_base
    class Base:
        def perform(self):
            return threading.get_ident()

    with count_hops() as hops:
        thread = await call_pair(Base(), "perform", "aperform")
    assert thread != threading.get_ident()
    assert hops.count == 1


@pytest.mark.parametrize("on_class", [False, True])
def test_user_hooks_are_detected_with_or_without_instance_storage(on_class):
    @bridge_base
    class Base:
        __slots__ = ()

        def perform(self):
            pass

    class Custom(Base):
        __slots__ = ()

        def perform(self):
            pass

    assert not user_defines(Base if on_class else Base(), "perform")
    assert user_defines(Custom if on_class else Custom(), "perform")


def test_instance_hook_changes_are_not_cached_on_the_class():
    @bridge_base
    class Base:
        def perform(self):
            pass

    first, second = Base(), Base()
    assert not user_defines(first, "perform", "other")
    first.perform = lambda: None
    assert user_defines(first, "perform", "other")
    assert not user_defines(second, "perform", "other")
    del first.perform
    assert not user_defines(first, "perform", "other")


@pytest.mark.parametrize("placement", ["default", "async", "sync", "instance"])
async def test_async_pairs_forward_arguments_without_a_sync_boundary(placement):
    calls = []

    async def perform(self, value, *, offset):
        calls.append(threading.get_ident())
        return value + offset

    @bridge_base
    class Base:
        def perform(self, value, *, offset):
            raise AssertionError("Synchronous hook was selected")

    class Custom(Base):
        pass

    if placement == "instance":
        obj = Custom()
        obj.aperform = functools.partial(perform, obj)
    else:
        cls = Base if placement == "default" else Custom
        setattr(cls, "perform" if placement == "sync" else "aperform", perform)
        obj = cls()
    with count_hops() as hops:
        assert await call_pair(obj, "perform", "aperform", 2, offset=3) == 5
    assert calls == [threading.get_ident()]
    assert hops.count == 0


@pytest.mark.parametrize("default", [False, True])
async def test_sync_wrappers_of_async_pairs_keep_the_worker_boundary(default):
    calls = []

    async def perform(value, *, offset):
        calls.append(("body", threading.get_ident()))
        return value + offset

    @bridge_base
    class Base:
        pass

    class Custom(Base):
        pass

    def wrapper(self, value, *, offset):
        calls.append(("wrapper", threading.get_ident()))
        return perform(value, offset=offset)

    cls = Base if default else Custom
    cls.aperform = wrapper
    with count_hops() as hops:
        assert await call_pair(cls(), "perform", "aperform", 2, offset=3) == 5
    assert calls[0][0] == "wrapper"
    assert calls[0][1] != threading.get_ident()
    assert calls[1] == ("body", threading.get_ident())
    assert hops.count == 1


async def test_missing_custom_async_descriptor_does_not_select_sync_fallback():
    class Custom:
        def perform(self):
            raise AssertionError("Missing custom async hooks must raise")

        @property
        def aperform(self):
            raise AttributeError("Unavailable hook")

    with pytest.raises(AttributeError, match="Unavailable hook"):
        await call_pair(Custom(), "perform", "aperform")


async def test_pure_sync_fallback_without_async_default_stays_inline():
    @bridge_base
    class Base:
        @async_safe
        def perform(self, value, *, offset):
            return value + offset, threading.get_ident()

    with count_hops() as hops:
        value, thread = await call_pair(Base(), "perform", "aperform", 2, offset=3)
    assert (value, thread) == (5, threading.get_ident())
    assert hops.count == 0
