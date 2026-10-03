"""
Primitives shared by every aiodrf module.

Django's standard ORM adapts synchronous database work to worker threads.
Cache and session behavior depends on the selected backend; native async
clients can perform I/O without that adaptation. aiodrf runs known nonblocking
work inline, groups blocking operations through :func:`run_sync`, and awaits
genuinely asynchronous hooks and clients.

Most aiodrf hooks come in pairs, following Django's naming convention
(``authenticate`` / ``aauthenticate``, ``save`` / ``asave``). :func:`resolve_pair`
decides which member of a pair a class actually implements, so code migrated
from DRF keeps working when only the synchronous member is overridden.
"""

import asyncio
import contextvars
import enum
import functools
import inspect
import sys
import threading
import types
import weakref
from collections.abc import Awaitable, Callable, Generator
from contextlib import contextmanager
from inspect import iscoroutinefunction
from typing import Any, Protocol, TypeVar, cast

from asgiref.sync import async_to_sync, sync_to_async
from fastdrf.utils import class_cache as _class_cache

from aiodrf.settings import aiodrf_settings

# The extension API (docs/guides/releasing.md, "API inventory"). The rest of
# this module is aiodrf's machinery: importable by aiodrf's own modules and
# contribs, not promised to projects.
__all__ = [
    "async_safe",
    "count_hops",
    "register_pure",
    "register_pure_method",
    "run_sync",
]


# -- Hops ---------------------------------------------------------------------


T_co = TypeVar("T_co", covariant=True)


class HopCounter:
    """Function names recorded while one :func:`count_hops` scope is active."""

    __slots__ = ("__weakref__", "_active", "_lock", "calls")

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._active = True
        self._lock = threading.Lock()

    def _record(self, name: str) -> None:
        # Child tasks and sync workers inherit the context. Serialize the
        # last append with closing so no child extends a completed scope.
        with self._lock:
            if self._active:
                self.calls.append(name)

    def _close(self) -> None:
        with self._lock:
            self._active = False

    @property
    def count(self) -> int:
        # Derived from ``calls``: ``list.append`` is atomic, a separate
        # ``count += 1`` would lose updates between threads.
        return len(self.calls)

    def __repr__(self) -> str:
        return f"<HopCounter count={self.count}>"


# A copied task context must not own the diagnostic after its block exits.
# The context manager, not an inherited context, keeps the counter alive.
_hop_counter: contextvars.ContextVar[weakref.ReferenceType[HopCounter] | None] = (
    contextvars.ContextVar("aiodrf_hop_counter", default=None)
)


def run_sync[**P, T](func: Callable[P, T], /) -> Callable[P, Awaitable[T]]:
    """
    Return an awaitable wrapper that runs ``func`` in the request's sync thread.

    This is the single place where aiodrf leaves the event loop, which keeps
    the hop budget measurable (see :func:`count_hops`). A hop is counted
    each time the wrapper is called, in the context of that call.
    """
    wrapped = sync_to_async(func, thread_sensitive=True)
    # Callable repr() may include credentials, request data, or execute code.
    name = getattr(func, "__qualname__", None) or type(func).__qualname__

    async def hop(*args: P.args, **kwargs: P.kwargs) -> T:
        reference = _hop_counter.get()
        if reference is not None:
            counter = reference()
            if counter is not None:
                counter._record(name)
            del counter  # Do not retain a diagnostic while the worker awaits I/O.
        return await wrapped(*args, **kwargs)

    return hop


@contextmanager
def count_hops() -> Generator[HopCounter, None, None]:
    """
    Count the thread hops aiodrf performs inside the block::

        with count_hops() as hops:
            response = await client.get("/books/")
        assert hops.count <= 2

    Inherited tasks record only until this block exits. Names are retained
    for inspection, so use a bounded operation rather than a server lifespan.
    """
    counter = HopCounter()
    token = _hop_counter.set(weakref.ref(counter))
    try:
        yield counter
    finally:
        counter._close()
        _hop_counter.reset(token)


async def maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def run_sync_and_await(
    func: Callable[..., Any], /, *args: Any, **kwargs: Any
) -> Any:
    """
    Call ``func`` in one hop (:func:`run_sync`) and await what it returns
    if that is awaitable: a synchronous wrapper of a coroutine function runs
    its own code in the worker and the coroutine on the event loop. If the
    caller is cancelled during the hop, the coroutine is closed, not started.
    """
    returned = []
    cancelled = threading.Event()

    def call() -> Any:
        result = func(*args, **kwargs)
        if inspect.iscoroutine(result):
            returned.append(result)
            if cancelled.is_set():
                result.close()
        return result

    # The hop is counted under ``func``'s name.
    call.__qualname__ = getattr(func, "__qualname__", None) or type(func).__qualname__
    try:
        result = await run_sync(call)()
    except asyncio.CancelledError:
        cancelled.set()
        for coroutine in returned:
            coroutine.close()
        raise
    return await maybe_await(result)


def is_async_callable(func: Callable[..., Any]) -> bool:
    """
    Return True if calling ``func`` produces an awaitable.

    Decorators that use ``functools.wraps`` (for example drf-spectacular's
    ``extend_schema_view``) hide the coroutine function behind a sync wrapper,
    so the ``__wrapped__`` chain is followed as well. That tells what the
    call returns, not that it may be made on the event loop: see
    :func:`awaits_inline`.
    """
    if awaits_inline(func):
        return True
    try:
        unwrapped = inspect.unwrap(func)
    except ValueError:
        return False
    return unwrapped is not func and iscoroutinefunction(unwrapped)


def awaits_inline(func: Callable[..., Any]) -> bool:
    """
    Return True if ``func`` may be called on the event loop and what it
    returns awaited there: a coroutine function (a ``functools.partial`` of
    one, or a bound method), a callable marked with ``markcoroutinefunction``,
    or a wrapper known to only call a coroutine function.

    A synchronous wrapper of any other kind runs code of its own before it
    returns the coroutine, and that code may block: it is called in a worker,
    and the coroutine is awaited on the event loop.
    """
    # Ordinary async hooks have exact Python function/method types. Read their
    # code flag directly; partials, marked functions and callable objects still
    # need inspect's general classification. Do not follow __wrapped__ here:
    # a synchronous decorator can perform blocking work before returning.
    target = func.__func__ if type(func) is types.MethodType else func
    if (
        type(target) is types.FunctionType
        and target.__code__.co_flags & inspect.CO_COROUTINE
    ):
        return True
    if iscoroutinefunction(func):
        return True
    if (
        callable(func)
        and not inspect.isroutine(func)
        and iscoroutinefunction(type(func).__call__)
    ):
        return True
    wrapped = getattr(func, "__wrapped__", None)
    return (
        wrapped is not None and _is_transparent_wrapper(func) and awaits_inline(wrapped)
    )


async def invoke(func: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    """Await a callable, keeping an unknown synchronous prefix in the worker."""
    if awaits_inline(func):
        return await func(*args, **kwargs)
    return await run_sync_and_await(func, *args, **kwargs)


# Code of wrappers that call what they wrap and nothing else: drf-spectacular's
# ``extend_schema_view`` replaces an inherited view method with one
# (``drf_spectacular.drainage.isolate_view_method``). Found once that module
# is imported; until then no such wrapper exists.
_transparent_wrappers: set[types.CodeType] = set()


def _is_transparent_wrapper(func: object) -> bool:
    if not _transparent_wrappers:
        drainage = sys.modules.get("drf_spectacular.drainage")
        if drainage is not None:
            constants = drainage.isolate_view_method.__code__.co_consts
            _transparent_wrappers.update(c for c in constants if inspect.iscode(c))
    return getattr(func, "__code__", None) in _transparent_wrappers


class ClassCached(Protocol[T_co]):
    """The return type of :func:`class_cache`: the function and its cache control."""

    def __call__(self, cls: type, /, *args: Any) -> T_co: ...

    def cache_clear(self) -> None: ...

    def cache_size(self) -> int: ...


def class_cache[T](func: Callable[..., T]) -> ClassCached[T]:
    """
    Memoize class metadata with weak keys and bounded publication:
    django-fastdrf's ``class_cache``, which maintains it, with its type.

    ``functools.cache`` holds a strong reference to every class it has seen.
    DRF creates classes at request time (``Meta.depth`` builds a nested
    serializer class for each serializer instance), so such a cache would
    grow with every request. At most ``fastdrf.utils.CLASS_CACHE_SIZE``
    entries are published before eviction.
    """
    return cast(ClassCached[T], _class_cache(func))


# -- Purity -------------------------------------------------------------------


# Per-class caches of decisions made from ``user_defines``, ``resolve_pair``
# and ``is_pure`` answers: dropped with them (``depends_on_classification``).
_DEPENDENTS: list[ClassCached[Any]] = []


def depends_on_classification[C: ClassCached[Any]](cached: C) -> C:
    """
    Register a :func:`class_cache` whose answers are made from what this
    module classifies (overrides, pairs, purity), so that a declaration or a
    settings change made later drops them as it drops those answers.
    """
    _DEPENDENTS.append(cached)
    return cached


def _clear_dependents() -> None:
    for cached in _DEPENDENTS:
        cached.cache_clear()


class _PureRegistry:
    """
    What was declared pure: by ``async_safe``, ``register_pure`` and
    ``register_pure_method``. ``AIODRF["PURE_POLICIES"]`` and
    ``INLINE_RENDERERS`` are read from the settings. :func:`is_pure` keeps
    its answers per class; every declaration and every settings change
    calls :meth:`changed`, which drops them.
    """

    def __init__(self) -> None:
        self.classes: weakref.WeakSet[type] = weakref.WeakSet()
        # Explicit callable registrations belong to AppConfig.ready(). They
        # may be non-weak-referenceable callable objects and are app-owned.
        self.functions: set[Callable[..., object]] = set()
        self.methods: weakref.WeakKeyDictionary[type, frozenset[str]] = (
            weakref.WeakKeyDictionary()
        )
        # Pure classes, and (class, method) pairs, that call nothing a
        # subclass could override.
        self.leaf_classes: weakref.WeakSet[type] = weakref.WeakSet()
        self.leaf_methods: weakref.WeakKeyDictionary[type, frozenset[str]] = (
            weakref.WeakKeyDictionary()
        )

    def changed(self) -> None:
        _is_pure.cache_clear()
        _clear_dependents()


_pure = _PureRegistry()

# Registrations belong at import time (module level, ``AppConfig.ready()``):
# requests read these sets without a lock.


def async_safe[T](obj: T) -> T:
    """
    Declare that a function, or every method a class defines itself, performs
    no blocking I/O and may run on the event loop.

    The declaration covers what the code *calls* too. A subclass is
    therefore only trusted while it adds plain data (``message = "..."``);
    one that defines methods of its own has to be declared itself::

        @async_safe
        class IsOwner(BasePermission):
            def has_object_permission(self, request, view, obj):
                return obj.owner_id == request.user.pk
    """
    if inspect.isclass(obj):
        _pure.classes.add(obj)
    else:
        obj._aiodrf_async_safe = True  # type: ignore[attr-defined]
    _pure.changed()
    return obj


def register_pure(*objects: type | Callable[..., Any], leaf: bool = False) -> None:
    """
    Register third-party classes or functions as :func:`async_safe` without
    decorating (and thereby modifying) them.

    ``leaf=True`` additionally states that the methods of the classes call
    no other method of the object, so subclasses that add methods keep the
    inherited ones pure (``IsAuthenticated.has_permission`` stays inline in
    a subclass that adds ``has_object_permission``).
    """
    for obj in objects:
        if inspect.isclass(obj):
            _pure.classes.add(obj)
            if leaf:
                _pure.leaf_classes.add(obj)
        else:
            _pure.functions.add(obj)
    _pure.changed()


def register_pure_method(cls: type, *names: str, leaf: bool = False) -> None:
    """
    Register individual methods of ``cls`` as :func:`async_safe`.

    ``leaf=True`` states that the methods call no other method of the
    object, so subclasses that add methods keep them pure (see
    :func:`register_pure`).
    """
    _pure.methods[cls] = _pure.methods.get(cls, frozenset()).union(names)
    if leaf:
        _pure.leaf_methods[cls] = _pure.leaf_methods.get(cls, frozenset()).union(names)
    _pure.changed()


def is_pure_function(func: Callable[..., Any]) -> bool:
    return getattr(func, "_aiodrf_async_safe", False) or func in _pure.functions


def definer(cls: type, name: str) -> type | None:
    """
    Return the class in ``cls.__mro__`` whose ``__dict__`` defines ``name``,
    looking through transparent mixins (:func:`_transparent`).
    """
    for klass in cls.__mro__:
        if name in klass.__dict__ and klass not in _TRANSPARENT:
            return klass
    return None


# Mixins whose members only wrap ``super()``'s, adding nothing a caller could
# observe but a side channel (``aiodrf.contrib.opentelemetry``'s spans).
_TRANSPARENT: weakref.WeakSet[type] = weakref.WeakSet()


def _transparent[T](cls: type[T]) -> type[T]:
    """
    Mark a mixin as transparent: which member of a sync/async pair a view
    implements, whether it overrides a hook and whether it is pure are
    decided as if the mixin were not there. Its members still run, since
    attribute lookup finds them first. Register at import time.

    Used by ``contrib.opentelemetry.TracingMixin``; the registry does not
    own the registered classes.
    """
    _TRANSPARENT.add(cls)
    _resolve_class_pair.cache_clear()
    _user_defines.cache_clear()
    _is_pure.cache_clear()
    _clear_dependents()
    return cls


def is_pure(obj: object, name: str) -> bool:
    """
    Return True if ``obj.<name>`` may be called inline on the event loop.

    Purity is decided by the class that *defines* the method, so a subclass
    that overrides it with database access is not pure by accident. It also
    has to cover what the method calls (``SearchFilter.filter_queryset``
    calls ``get_search_fields``; ``JSONRenderer.render`` uses
    ``encoder_class``): the classes between ``type(obj)`` and the defining
    class may only add plain data, unless they are declared pure themselves
    or the defining class is registered as a leaf.
    """
    if isinstance(obj, type):
        cls = obj
    else:
        if name in getattr(obj, "__dict__", {}):
            return False
        cls = type(obj)
    return _is_pure(cls, name)


@class_cache
def _is_pure(cls: type, name: str) -> bool:
    klass = definer(cls, name)
    if klass is None or not _is_declared_pure(klass, name):
        return False
    if klass in _pure.leaf_classes or name in _pure.leaf_methods.get(klass, ()):
        return True
    for subclass in cls.__mro__:
        if subclass is klass:
            return True
        if subclass in _TRANSPARENT:
            continue
        if _defines_code(subclass) and not _is_declared_pure(subclass, None):
            return False
    return True


def _is_declared_pure(klass: type, name: str | None) -> bool:
    if klass in _pure.classes or klass.__dict__.get("async_safe") is True:
        return True
    if klass in aiodrf_settings.pure_classes:
        return True
    if name is None:
        return False
    return name in _pure.methods.get(klass, ()) or is_pure_function(
        klass.__dict__[name]
    )


# Dunder entries every class body has; they are not code a method could call.
_CLASS_BOILERPLATE = frozenset(
    {
        "__module__",
        "__qualname__",
        "__doc__",
        "__dict__",
        "__weakref__",
        "__annotations__",
        "__firstlineno__",
        "__static_attributes__",
        "__annotate_func__",
        "__annotations_cache__",
        "__parameters__",
        "__orig_bases__",
        "__slots__",
    }
)


def _defines_code(klass: type) -> bool:
    """True if ``klass`` itself defines anything executable: functions,
    descriptors (reading one runs its ``__get__``), or class-valued
    attributes such as ``encoder_class``."""
    return any(
        callable(value) or hasattr(type(value), "__get__")
        for key, value in klass.__dict__.items()
        if key not in _CLASS_BOILERPLATE
    )


# -- Method pairs -------------------------------------------------------------

# Framework modules register defaults at import time; membership must not
# keep dynamically constructed extension classes alive.
_BRIDGE_BASES: weakref.WeakSet[type] = weakref.WeakSet()


def bridge_base[T](cls: type[T]) -> type[T]:
    """
    Mark ``cls`` as a framework base class.

    Methods defined on a bridge base are defaults rather than user code:
    :func:`resolve_pair` ignores them when deciding which member of a
    sync/async pair the user implemented.
    """
    _BRIDGE_BASES.add(cls)
    # Resolutions made before this registration treated ``cls`` as user code.
    _resolve_class_pair.cache_clear()
    _user_defines.cache_clear()
    _clear_dependents()
    # The optimizations aiodrf builds on (django-fastdrf) treat it as
    # framework code too, and drop what they decided before. Imported here:
    # fastdrf's module imports DRF's, which this module does not need.
    from fastdrf.utils import framework_base

    framework_base(cls)
    return cls


def is_bridge_base(cls: type | None) -> bool:
    return cls in _BRIDGE_BASES


def user_defines(obj: object, *names: str) -> bool:
    """
    Return True if any of the hooks ``names`` of ``obj`` is user code rather
    than a framework default. Framework defaults only build objects and may
    run on the event loop; what a project wrote for DRF runs in a thread.
    """
    if isinstance(obj, type):
        cls = obj
    else:
        state = getattr(obj, "__dict__", {})
        if not state.keys().isdisjoint(names):
            return True
        cls = type(obj)
    return _user_defines(cls, names)


class Impl(enum.Enum):
    #: Only the async member (``aauthenticate``) is user code.
    ASYNC = "async"
    #: Only the sync member (``authenticate``) is user code.
    SYNC = "sync"
    #: The sync member is itself an ``async def`` (adrf style).
    SYNC_IS_ASYNC = "sync_is_async"
    #: Neither member is user code; use the framework default.
    BASE = "base"


# django-fastdrf is the package aiodrf builds on: its classes (the msgspec
# renderer, the responses) are framework code here too.
_FRAMEWORK_PACKAGES = frozenset(
    {"builtins", "django", "rest_framework", "aiodrf", "fastdrf"}
)


def is_framework_class(klass: type) -> bool:
    """
    True for Django's, DRF's, django-fastdrf's and aiodrf's classes, and
    registered bridge bases.
    """
    return (
        klass in _BRIDGE_BASES
        or klass.__module__.split(".", 1)[0] in _FRAMEWORK_PACKAGES
    )


@class_cache
def _user_defines(cls: type, names: tuple[str, ...]) -> bool:
    for name in names:
        klass = definer(cls, name)
        if klass is not None and not is_framework_class(klass):
            return True
    return False


def resolve_pair(obj: object, sync_name: str, async_name: str) -> Impl:
    """Resolve instance overrides first, then the nearest custom class member."""
    if isinstance(obj, type):
        cls = obj
    else:
        cls = type(obj)
        state = getattr(obj, "__dict__", {})
        if async_name in state:
            return Impl.ASYNC
        if sync_name in state:
            return (
                Impl.SYNC_IS_ASYNC
                if is_async_callable(getattr(obj, sync_name))
                else Impl.SYNC
            )
    return _resolve_class_pair(cls, sync_name, async_name)


@class_cache
def _resolve_class_pair(cls: type, sync_name: str, async_name: str) -> Impl:
    sync_definer = definer(cls, sync_name)
    async_definer = definer(cls, async_name)
    user_sync = sync_definer is not None and sync_definer not in _BRIDGE_BASES
    user_async = async_definer is not None and async_definer not in _BRIDGE_BASES

    if user_sync and user_async:
        # Both are overridden: the one closest to ``cls`` wins, exactly like
        # ordinary attribute lookup.
        mro = cls.__mro__
        if mro.index(async_definer) <= mro.index(sync_definer):
            return Impl.ASYNC
    elif user_async:
        return Impl.ASYNC
    if user_sync:
        if is_async_callable(sync_definer.__dict__[sync_name]):
            return Impl.SYNC_IS_ASYNC
        return Impl.SYNC
    return Impl.BASE


resolve_pair.cache_clear = _resolve_class_pair.cache_clear  # type: ignore[attr-defined]
resolve_pair.cache_size = _resolve_class_pair.cache_size  # type: ignore[attr-defined]


async def call_pair(
    obj: object, sync_name: str, async_name: str, /, *args: Any, **kwargs: Any
) -> Any:
    """
    Call whichever member of a sync/async method pair ``obj`` implements.

    Synchronous implementations run inline when they are declared pure and
    in one thread hop otherwise.
    """
    impl = resolve_pair(obj, sync_name, async_name)
    if impl is Impl.ASYNC:
        return await invoke(getattr(obj, async_name), *args, **kwargs)
    if impl is Impl.BASE:
        try:
            method = getattr(obj, async_name)
        except AttributeError:
            pass
        else:
            # Bind descriptors once; checking hasattr() first also executes them.
            return await invoke(method, *args, **kwargs)
    method = getattr(obj, sync_name)
    if impl is Impl.SYNC_IS_ASYNC:
        if awaits_inline(method):
            return await method(*args, **kwargs)
        # A synchronous wrapper of a coroutine function (``is_async_callable``).
        return await run_sync_and_await(method, *args, **kwargs)
    if is_pure(obj, sync_name):
        return await maybe_await(method(*args, **kwargs))
    return await run_sync_and_await(method, *args, **kwargs)


def bridge_to_async(
    obj: object,
    sync_name: str,
    async_name: str,
    target: Any,
    /,
    *args: Any,
    advice: str = "",
) -> Any:
    """
    Run ``target`` (the async member ``async_name`` of ``obj``, or a
    dispatcher for it) for the synchronous caller of ``sync_name``.

    ``async_to_sync`` refuses to run on the event loop with a message that
    names neither the member nor the fix; the usual cause is DRF's habit of
    calling ``super().<sync member>()`` inside the async member.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return async_to_sync(target)(*args)
    name = type(obj).__qualname__
    raise RuntimeError(
        f"{name}.{sync_name}() was called on the event loop, but {name} "
        f"implements it asynchronously: await `{async_name}()` instead. Inside "
        f"an async member, call `await super().{async_name}(...)`, not "
        f"`super().{sync_name}(...)`.{advice}"
    )


def bridges_to[**P, T](async_name: str) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """
    Decorate the synchronous member of a sync/async pair on a framework class.

    DRF calls the synchronous member wherever it works synchronously (an
    ``initial()`` override calling ``super()``, the browsable API, schema
    generation). When a class implements only the async member
    (:func:`resolve_pair` gives ``ASYNC``), the decorated method hands the
    call over to it through ``async_to_sync``; otherwise its own body runs.
    ``async_to_sync`` works from a worker thread and fails loudly on the
    event loop, where the async member must be awaited instead.
    """

    def decorate(method: Callable[P, T]) -> Callable[P, T]:
        sync_name = method.__name__

        @functools.wraps(method)
        def bridge(*args: P.args, **kwargs: P.kwargs) -> T:
            obj, *rest = args
            if resolve_pair(obj, sync_name, async_name) is Impl.ASYNC:
                target = getattr(obj, async_name)
                return bridge_to_async(
                    obj,
                    sync_name,
                    async_name,
                    functools.partial(invoke, target, *rest, **kwargs),
                )
            return method(*args, **kwargs)

        return bridge

    return decorate


def call_pair_sync(
    obj: object, sync_name: str, async_name: str, /, *args: Any, **kwargs: Any
) -> Any:
    """
    :func:`call_pair` for synchronous callers.

    DRF calls the synchronous member of a pair wherever it works
    synchronously (a view's ``initial()`` override, the browsable API,
    schema generation). An object that only implements the async member, or
    whose "sync" member is a coroutine function, must still take effect
    there: an unawaited coroutine is truthy, so a permission would allow.
    ``async_to_sync`` works from a worker thread and fails loudly on the
    event loop, where :func:`call_pair` must be used.
    """
    impl = resolve_pair(obj, sync_name, async_name)
    if impl is Impl.ASYNC:
        method = getattr(obj, async_name)
        return async_to_sync(invoke)(method, *args, **kwargs)
    method = getattr(obj, sync_name)
    if impl is Impl.SYNC_IS_ASYNC:
        return async_to_sync(invoke)(method, *args, **kwargs)
    result = method(*args, **kwargs)
    if inspect.isawaitable(result):
        return async_to_sync(maybe_await)(result)
    return result


def uses_sync(obj: object, sync_name: str, async_name: str) -> bool:
    """
    Return True if ``obj`` can be driven entirely through its sync member.

    That is the case when the class has no async implementation of its own,
    which lets callers batch several such objects into a single hop.
    """
    impl = resolve_pair(obj, sync_name, async_name)
    if impl is Impl.SYNC:
        return True
    return impl is Impl.BASE and not hasattr(obj, async_name)
