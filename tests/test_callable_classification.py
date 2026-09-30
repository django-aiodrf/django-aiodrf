"""Native coroutine detection retains marked and wrapped callable semantics."""

import functools
import inspect
import types

import pytest

from aiodrf import utils


@pytest.mark.parametrize("bound", [False, True])
def test_native_functions_do_not_need_general_callable_inspection(monkeypatch, bound):
    async def function(self=None):
        return 1

    target = types.MethodType(function, object()) if bound else function

    def unexpected(value):
        pytest.fail("An exact native coroutine function has its own code flags")

    monkeypatch.setattr(utils, "iscoroutinefunction", unexpected)
    assert utils.awaits_inline(target)


def test_synchronous_wrapper_is_not_mistaken_for_its_async_target():
    async def function():
        return 1

    @functools.wraps(function)
    def wrapped():
        return function()

    assert not utils.awaits_inline(wrapped)
    assert not utils.awaits_inline(types.MethodType(wrapped, object()))
    assert utils.awaits_inline(functools.partial(function))


def test_method_bound_to_callable_object_uses_general_inspection():
    class Callable:
        async def __call__(self, owner):
            return owner

    target = types.MethodType(Callable(), object())
    # This is not a Python function's bound method. Classification must not
    # assume __func__.__code__ exists or run the callable to discover it.
    assert not utils.awaits_inline(target)


def test_marking_a_function_is_observed_without_a_callable_cache():
    def function():
        return 1

    assert not utils.awaits_inline(function)
    inspect.markcoroutinefunction(function)
    assert utils.awaits_inline(function)
