"""
Settings for aiodrf live in the ``AIODRF`` setting::

    AIODRF = {
        "SERIALIZER_BACKEND": "msgspec",
        "FETCH_MODE": "peers",
    }

Everything DRF already configures (authentication, permission, renderer
classes, ...) stays in ``REST_FRAMEWORK``; aiodrf works with DRF's own policy
classes and never asks for them to be duplicated here.
"""

import threading
from collections.abc import Mapping
from inspect import isasyncgenfunction, iscoroutinefunction
from typing import Any, cast

import asgiref
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.signals import setting_changed
from django.utils.version import get_version_tuple
from rest_framework.settings import APISettings, import_from_string

ASGIREF_VERSION = get_version_tuple(asgiref.__version__)

DEFAULTS: dict[str, object] = {
    # A zero-argument async context manager factory, or its dotted path.
    # Used only by aiodrf.asgi.get_asgi_application(); no resources are
    # created by importing settings or running Django's system checks.
    "LIFESPAN": None,
    # Only affects explicitly installed aiodrf.unsafe.middleware subclasses.
    # Django groups their sync hooks; a worker stays occupied while a view awaits.
    # Configure before constructing the ASGI/WSGI handler, then restart workers.
    "UNSAFE_SYNC_MIDDLEWARE": False,
    # Where user-defined synchronous validation code runs (``validate_<field>``,
    # ``validate``, validators and fields aiodrf cannot classify):
    #   "thread": in a worker thread, once (the default),
    #   "inline": on the event loop; the project asserts that it never blocks.
    "VALIDATION_UNKNOWN": "thread",
    # Where ``serializer.data`` is produced when aiodrf is asked for it from
    # async code (generic actions represent inside the hop that fetched):
    #   "thread": in a worker thread, once (the default),
    #   "inline": on the event loop; the project asserts that the instances
    #             are loaded (``select_related`` / ``prefetch_related``).
    # Nothing is tried inline first and repeated in a thread.
    "REPRESENTATION_MODE": "thread",
    # Run the default synchronous save (``create`` / ``update`` with their
    # many-to-many writes) in ``transaction.atomic``.
    "ATOMIC_SAVE": True,
    # Renderer classes whose ``render`` is pure CPU work (dotted paths), in
    # addition to the built-in JSON renderers.
    "INLINE_RENDERERS": [],
    # Permission, authentication, throttle and filter classes to treat as
    # ``@async_safe`` (dotted paths).
    "PURE_POLICIES": [],
    # Django 6.1+ fetch mode applied to querysets of generic views:
    # None, "peers" (FETCH_PEERS) or "raise" (FETCH_RAISE).
    "FETCH_MODE": None,
    # Serializer backend used by ``aiodrf.contrib`` compilers:
    # "drf", "msgspec", "pydantic" or "python" (output only, no dependency).
    "SERIALIZER_BACKEND": "drf",
    # "strict" only compiles serializers whose output is identical to DRF's;
    # "fast" also accepts the documented differences.
    "SERIALIZER_BACKEND_PARITY": "strict",
    # What happens to output the backend cannot compile: "drf" uses
    # DRF for it, "error" raises ImproperlyConfigured with the reason (for
    # tests and serializers that must not fall back silently).
    # ``Meta.serializer_backend_fallback`` overrides it per serializer.
    # Input recognition always falls back to DRF when it declines.
    "SERIALIZER_BACKEND_FALLBACK": "drf",
    # What a view's serializer may be: "drf" (a DRF serializer), "msgspec"
    # (a Struct or ``MsgspecSerializer``), "pydantic" (a model or
    # ``PydanticSerializer``). Checked when a URL is built (``as_view``), by
    # ``manage.py check``, and for a serializer chosen per request.
    "ALLOWED_SERIALIZER_BACKENDS": ["drf", "msgspec", "pydantic"],
    # Build the fields of an aiodrf ModelSerializer class once and give each
    # instance a deep copy, for classes whose fields depend only on the class
    # (no field-building hooks, no ``Meta.depth``). ``Meta`` and models must
    # not change at runtime.
    "CACHE_SERIALIZER_FIELDS": False,
    # Opt-in copying of exact built-in scalar fields from the cached template.
    # "compiled" additionally plans recursive constructor-argument copies.
    # Custom copying and unsupported values retain DRF deepcopy.
    "FIELD_COPY_MODE": "deepcopy",
    # Look up the items of ``PrimaryKeyRelatedField(many=True)`` input with
    # one query (``pk__in``) instead of one ``get(pk=...)`` per item when
    # aiodrf validates. Instances and errors are DRF's.
    "BATCH_RELATED_LOOKUPS": False,
    # Serve the adrf modules from aiodrf (aiodrf.contrib.adrf_compat), so a
    # project written for adrf runs unchanged. Read once, when Django loads
    # aiodrf's app; adrf must not be installed.
    "ADRF_COMPAT": False,
    # Opt-in patches of DRF classes, by name (``aiodrf.contrib.monkeypatches``),
    # applied at startup.
    "MONKEYPATCHES": [],
    # ``aiodrf.asgi``: None starts a thread for each request's synchronous
    # code, as Django does; a number keeps up to that many idle request
    # threads to lend to later requests, one request at a time.
    "REQUEST_THREADS": None,
}

SERIALIZER_KINDS = ("drf", "msgspec", "pydantic")

# Lists of dotted paths. Classes are accepted as well.
IMPORT_STRINGS = ["INLINE_RENDERERS", "PURE_POLICIES"]

# Values that existed before and why they are gone, for the error message.
REMOVED_CHOICES = {
    "optimistic": (
        '"optimistic" ran code on the event loop and repeated it in a thread when it '
        'queried. Use "thread", or "inline" for code known not to block.'
    ),
}

CHOICES = {
    "FIELD_COPY_MODE": ("deepcopy", "clone", "compiled"),
    "VALIDATION_UNKNOWN": ("thread", "inline"),
    "REPRESENTATION_MODE": ("thread", "inline"),
    "FETCH_MODE": (None, "peers", "raise"),
    "SERIALIZER_BACKEND": ("drf", "msgspec", "pydantic", "python"),
    "SERIALIZER_BACKEND_PARITY": ("strict", "fast"),
    "SERIALIZER_BACKEND_FALLBACK": ("drf", "error"),
    "ADRF_COMPAT": (False, True),
}


def _choice(name: str, value: Any) -> str | None:
    allowed = CHOICES[name]
    if value not in allowed:
        hint = REMOVED_CHOICES.get(value, "") if isinstance(value, str) else ""
        return (
            f"Invalid value {value!r} for AIODRF[{name!r}]; expected one of "
            f"{', '.join(map(repr, allowed))}. {hint}"
        ).rstrip()
    return None


def _boolean(name: str, value: Any) -> str | None:
    if type(value) is not bool:
        return f"AIODRF[{name!r}] must be True or False, not {value!r}."
    return None


def _classes_or_paths(name: str, value: Any) -> str | None:
    if not isinstance(value, (list, tuple)):
        return f"AIODRF[{name!r}] must be a list of classes or dotted paths."
    if any(not isinstance(item, (str, type)) for item in value):
        return f"AIODRF[{name!r}] entries must be classes or dotted paths."
    return None


def _serializer_kinds(name: str, value: Any) -> str | None:
    if (
        not isinstance(value, (list, tuple))
        or not value
        or any(item not in SERIALIZER_KINDS for item in value)
    ):
        return (
            f"Invalid value {value!r} for AIODRF[{name!r}]; expected a non-empty list "
            f"of {', '.join(map(repr, SERIALIZER_KINDS))}."
        )
    return None


def _patch_names(name: str, value: Any) -> str | None:
    from aiodrf.contrib.monkeypatches import PATCHES

    if not isinstance(value, (list, tuple)) or any(
        item not in PATCHES for item in value
    ):
        return (
            f"Invalid value {value!r} for AIODRF[{name!r}]; expected a list "
            f"of {', '.join(map(repr, PATCHES))}."
        )
    return None


def _positive_or_none(name: str, value: Any) -> str | None:
    if value is not None and (type(value) is not int or value < 1):
        return (
            f"Invalid value {value!r} for AIODRF[{name!r}]; expected None "
            "or a positive integer."
        )
    return None


def _unsafe_sync_middleware(name: str, value: Any) -> str | None:
    if error := _boolean(name, value):
        return error
    if value and ASGIREF_VERSION < (3, 12, 1):
        return (
            "AIODRF['UNSAFE_SYNC_MIDDLEWARE'] requires asgiref>=3.12.1. "
            "Older adapters have request-context and nested-executor issues; "
            "leave the option disabled or upgrade asgiref."
        )
    return None


def _lifespan(name: str, value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) and not callable(value):
        return "AIODRF['LIFESPAN'] must be None, a dotted path or a callable."
    if any(
        check(candidate)
        for check in (iscoroutinefunction, isasyncgenfunction)
        for candidate in (value, value.__call__ if callable(value) else None)
    ):
        return (
            "AIODRF['LIFESPAN'] must return an async context manager; "
            "decorate an async generator with @contextlib.asynccontextmanager."
        )
    return None


def _choice_of_booleans(name: str, value: Any) -> str | None:
    return _choice(name, value) or _boolean(name, value)


# One validator per setting: ``(name, value) -> message or None``.
VALIDATORS = {
    "LIFESPAN": _lifespan,
    "UNSAFE_SYNC_MIDDLEWARE": _unsafe_sync_middleware,
    "VALIDATION_UNKNOWN": _choice,
    "REPRESENTATION_MODE": _choice,
    "ATOMIC_SAVE": _boolean,
    "INLINE_RENDERERS": _classes_or_paths,
    "PURE_POLICIES": _classes_or_paths,
    "FETCH_MODE": _choice,
    "SERIALIZER_BACKEND": _choice,
    "SERIALIZER_BACKEND_PARITY": _choice,
    "SERIALIZER_BACKEND_FALLBACK": _choice,
    "ALLOWED_SERIALIZER_BACKENDS": _serializer_kinds,
    "CACHE_SERIALIZER_FIELDS": _boolean,
    "FIELD_COPY_MODE": _choice,
    "BATCH_RELATED_LOOKUPS": _boolean,
    "ADRF_COMPAT": _choice_of_booleans,
    "MONKEYPATCHES": _patch_names,
    "REQUEST_THREADS": _positive_or_none,
}


def setting_error(name: str, value: Any) -> str | None:
    """Validate a value before either runtime access or a system check uses it."""
    validator = VALIDATORS.get(name)
    return validator(name, value) if validator is not None else None


def resolve_lifespan(value: Any) -> Any:
    """Import and validate the factory without calling it."""
    if isinstance(value, str):
        value = import_from_string(value, "LIFESPAN")
        if not callable(value):
            raise ImproperlyConfigured("AIODRF['LIFESPAN'] must resolve to a callable.")
    if error := setting_error("LIFESPAN", value):
        raise ImproperlyConfigured(error)
    return value


class AioDRFSettings(APISettings):
    # Values are computed without a lock (importing a dotted path under one
    # could deadlock with an import in progress elsewhere) and published
    # under ``_reload_lock`` only if no ``reload()`` happened meanwhile: a
    # value read before a reload is otherwise cached after it, and is then
    # never cleared, because the reload also emptied ``_cached_attrs``.
    _generation = 0

    def _publish(self, name: str, value: object, started: int) -> None:
        with _reload_lock:
            if started == self._generation:
                self.__dict__[name] = value

    @property
    def user_settings(self) -> Mapping[str, Any]:
        # Returns what it read: between a ``hasattr`` check and a second
        # lookup another thread's ``reload()`` could remove the attribute.
        try:
            value = self.__dict__["_user_settings"]
        except KeyError:
            started = self._generation
            value = getattr(settings, "AIODRF", {})
            if isinstance(value, Mapping):
                self._publish("_user_settings", value, started)
        if not isinstance(value, Mapping):
            raise ImproperlyConfigured("AIODRF must be a mapping of settings.")
        return value

    def __getattr__(self, attr: str) -> Any:
        # DRF's ``__getattr__``, except that the value is validated *before*
        # it is cached (a cached attribute never reaches ``__getattr__``
        # again) and that import lists may contain classes.
        if attr not in self.defaults:
            raise AttributeError(f"Invalid AIODRF setting: {attr!r}")
        started = self._generation
        # ``defaults`` are aiodrf's, not the DRF keys its stubs declare.
        value = self.user_settings.get(
            attr, cast(Mapping[str, Any], self.defaults)[attr]
        )
        if error := setting_error(attr, value):
            raise ImproperlyConfigured(error)
        if attr == "LIFESPAN":
            value = resolve_lifespan(value)
        if attr in self.import_strings:
            value = [
                import_from_string(item, attr) if isinstance(item, str) else item
                for item in value
            ]
            if any(not isinstance(item, type) for item in value):
                raise ImproperlyConfigured(
                    f"AIODRF[{attr!r}] entries must resolve to classes."
                )
        with _reload_lock:
            if started == self._generation:
                self._cached_attrs.add(attr)
                self.__dict__[attr] = value
        return value

    @property
    def pure_classes(self) -> frozenset[type]:
        """The classes listed in ``PURE_POLICIES`` and ``INLINE_RENDERERS``."""
        try:
            return self.__dict__["_pure_classes"]
        except KeyError:
            started = self._generation
            classes = frozenset([*self.PURE_POLICIES, *self.INLINE_RENDERERS])
            self._publish("_pure_classes", classes, started)
            return classes

    def reload(self) -> None:
        # One reload at a time, and ``_cached_attrs`` is iterated on a copy:
        # DRF's ``reload()`` iterates it while ``__getattr__`` in another
        # thread may add to it ("Set changed size during iteration" without
        # the GIL).
        with _reload_lock:
            self._generation += 1
            for attr in list(self._cached_attrs):
                self.__dict__.pop(attr, None)
            self._cached_attrs.clear()
            self.__dict__.pop("_user_settings", None)
            self.__dict__.pop("_pure_classes", None)


_reload_lock = threading.Lock()
# DRF types ``defaults`` as its own settings; these are aiodrf's.
aiodrf_settings = AioDRFSettings(None, DEFAULTS, IMPORT_STRINGS)  # type: ignore[arg-type]


def reload_aiodrf_settings(*, setting: str, **kwargs: Any) -> None:
    if setting in ("AIODRF", "REST_FRAMEWORK"):
        aiodrf_settings.reload()
        # ``PURE_POLICIES`` and ``INLINE_RENDERERS`` decide purity too.
        from aiodrf.utils import _pure

        _pure.changed()


setting_changed.connect(reload_aiodrf_settings)
