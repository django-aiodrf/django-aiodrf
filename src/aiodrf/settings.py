"""
Settings for aiodrf live in the ``AIODRF`` setting::

    AIODRF = {
        "REPRESENTATION_MODE": "inline",
        "ATOMIC_SAVE": True,
    }

The optimizations aiodrf builds on (serializer backends, field caching,
related lookups, fetch modes) are django-fastdrf's and read from its
``FASTDRF`` setting. Everything DRF already configures (authentication, permission, renderer
classes, ...) stays in ``REST_FRAMEWORK``; aiodrf works with DRF's own policy
classes and never asks for them to be duplicated here.
"""

import threading
from collections.abc import Mapping
from typing import Any, cast

import asgiref
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.signals import setting_changed
from django.utils.version import get_version_tuple
from rest_framework.settings import APISettings, import_from_string

ASGIREF_VERSION = get_version_tuple(asgiref.__version__)

DEFAULTS: dict[str, object] = {
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

# Settings of the optimizations django-fastdrf provides, read from its
# ``FASTDRF`` setting since aiodrf builds on it.
MOVED_TO_FASTDRF = frozenset(
    {
        "SERIALIZER_BACKEND",
        "SERIALIZER_BACKEND_PARITY",
        "SERIALIZER_BACKEND_FALLBACK",
        "ALLOWED_SERIALIZER_BACKENDS",
        "CACHE_SERIALIZER_FIELDS",
        "FIELD_COPY_MODE",
        "BATCH_RELATED_LOOKUPS",
        "FETCH_MODE",
    }
)


def moved_settings_error(user_settings: Mapping[str, Any]) -> str | None:
    """The error for ``AIODRF`` keys that are now ``FASTDRF``'s, or None."""
    if "LIFESPAN" in user_settings:
        return "AIODRF['LIFESPAN'] moved to DJANGO_LIFESPAN; install django-aiodrf[lifespan]."
    moved = sorted(MOVED_TO_FASTDRF.intersection(user_settings))
    if not moved:
        return None
    return (
        f"AIODRF[{moved[0]!r}] is now FASTDRF[{moved[0]!r}]: django-fastdrf "
        f"provides it. Move {', '.join(map(repr, moved))} from AIODRF to FASTDRF."
    )


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
    "VALIDATION_UNKNOWN": ("thread", "inline"),
    "REPRESENTATION_MODE": ("thread", "inline"),
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


def _choice_of_booleans(name: str, value: Any) -> str | None:
    return _choice(name, value) or _boolean(name, value)


# One validator per setting: ``(name, value) -> message or None``.
VALIDATORS = {
    "UNSAFE_SYNC_MIDDLEWARE": _unsafe_sync_middleware,
    "VALIDATION_UNKNOWN": _choice,
    "REPRESENTATION_MODE": _choice,
    "ATOMIC_SAVE": _boolean,
    "INLINE_RENDERERS": _classes_or_paths,
    "PURE_POLICIES": _classes_or_paths,
    "ADRF_COMPAT": _choice_of_booleans,
    "MONKEYPATCHES": _patch_names,
    "REQUEST_THREADS": _positive_or_none,
}


def setting_error(name: str, value: Any) -> str | None:
    """Validate a value before either runtime access or a system check uses it."""
    validator = VALIDATORS.get(name)
    return validator(name, value) if validator is not None else None


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
        # Raised, not ignored: a server does not run the system checks, and
        # the setting would silently stop applying.
        if error := moved_settings_error(value):
            raise ImproperlyConfigured(error)
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
