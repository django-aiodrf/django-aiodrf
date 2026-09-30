"""Django system checks for aiodrf configuration and async contracts.

Registered callbacks retain Django's app_configs/keyword signature even when
a check concerns project settings rather than a particular application.
"""

import inspect
import os
from collections.abc import Iterator, Mapping, Sequence
from importlib.util import find_spec
from typing import Any

from django.apps import AppConfig, apps
from django.conf import settings
from django.core.checks import CheckMessage, Error, Tags, Warning, register
from django.core.exceptions import ImproperlyConfigured
from django.db import connections
from django.utils.module_loading import import_string

from aiodrf.compat import DJANGO_HAS_FETCH_MODES
from aiodrf.settings import (
    CHOICES,
    DEFAULTS,
    IMPORT_STRINGS,
    resolve_lifespan,
    setting_error,
)


@register(Tags.compatibility)
def check_settings(
    app_configs: Sequence[AppConfig] | None, **kwargs: Any
) -> list[CheckMessage]:
    # The settings object raises for these as well, but only when a value is
    # first used, which for a server is in the middle of a request.
    user_settings = getattr(settings, "AIODRF", {})
    if not isinstance(user_settings, Mapping):
        return [Error("AIODRF must be a mapping of settings.", id="aiodrf.E003")]
    errors: list[CheckMessage] = []
    invalid = set()
    for name in DEFAULTS:
        value = user_settings.get(name, DEFAULTS[name])
        if message := setting_error(name, value):
            invalid.add(name)
            choice = name in CHOICES or name == "ALLOWED_SERIALIZER_BACKENDS"
            errors.append(Error(message, id="aiodrf.E001" if choice else "aiodrf.E006"))
    for name in IMPORT_STRINGS:
        if name in invalid:
            continue
        for item in user_settings.get(name, ()):
            if not isinstance(item, str):
                continue
            try:
                klass = import_string(item)
            except ImportError as exc:
                errors.append(
                    Error(f"AIODRF[{name!r}] lists {item!r}: {exc}", id="aiodrf.E002")
                )
            else:
                if not isinstance(klass, type):
                    errors.append(
                        Error(
                            f"AIODRF[{name!r}] lists {item!r}, which is not a class.",
                            id="aiodrf.E007",
                        )
                    )
    if "LIFESPAN" not in invalid:
        try:
            resolve_lifespan(user_settings.get("LIFESPAN"))
        except ImportError as exc:
            errors.append(Error(str(exc), id="aiodrf.E002"))
        except ImproperlyConfigured as exc:
            errors.append(Error(str(exc), id="aiodrf.E006"))
    backend = user_settings.get("SERIALIZER_BACKEND", "drf")
    if backend in ("msgspec", "pydantic") and find_spec(backend) is None:
        errors.append(
            Error(
                f"AIODRF['SERIALIZER_BACKEND'] is {backend!r}, which is not installed.",
                hint=f"pip install django-aiodrf[{backend}]",
                id="aiodrf.E004",
            )
        )
    if (
        "FETCH_MODE" not in invalid
        and user_settings.get("FETCH_MODE") is not None
        and not DJANGO_HAS_FETCH_MODES
    ):
        errors.append(
            Warning(
                "AIODRF['FETCH_MODE'] needs Django 6.1 and is ignored.",
                hint="Upgrade Django, or remove the setting.",
                id="aiodrf.W009",
            )
        )
    errors.extend(
        Warning(
            f"AIODRF[{name!r}] is not a setting of aiodrf and is ignored.",
            hint=f"The settings are {', '.join(sorted(DEFAULTS))}.",
            id="aiodrf.W003",
        )
        for name in sorted(set(user_settings) - set(DEFAULTS), key=str)
    )
    return errors


@register(Tags.compatibility)
def check_atomic_requests(
    app_configs: Sequence[AppConfig] | None, **kwargs: Any
) -> list[CheckMessage]:
    return [
        Warning(
            f"ATOMIC_REQUESTS is enabled for database {alias!r}.",
            hint=(
                "Django cannot wrap async views in a transaction and raises "
                "for every request to an async view that is not decorated "
                "with @transaction.non_atomic_requests. aiodrf runs each "
                'serializer save in its own transaction (AIODRF["ATOMIC_SAVE"]).'
            ),
            id="aiodrf.W002",
        )
        for alias in connections
        if connections.settings[alias].get("ATOMIC_REQUESTS")
    ]


@register(Tags.compatibility)
def check_mongodb_transactions(
    app_configs: Sequence[AppConfig] | None, **kwargs: Any
) -> list[CheckMessage]:
    # Settings only: django-mongodb-backend need not be importable here.
    # An invalid ATOMIC_SAVE is check_settings' to report.
    user_settings = getattr(settings, "AIODRF", {})
    if (
        not isinstance(user_settings, Mapping)
        or user_settings.get("ATOMIC_SAVE") is False
    ):
        return []
    if apps.is_installed("aiodrf.contrib.mongodb"):
        return []
    return [
        Warning(
            f"AIODRF['ATOMIC_SAVE'] does not roll back saves on the MongoDB database {alias!r}.",
            hint=(
                "django-mongodb-backend makes Django's transaction.atomic() a no-op. "
                'Add "aiodrf.contrib.mongodb" to INSTALLED_APPS to save in the '
                "backend's own transaction."
            ),
            obj=alias,
            id="aiodrf.W007",
        )
        for alias, config in connections.settings.items()
        if config.get("ENGINE", "").partition(".")[0] == "django_mongodb_backend"
    ]


@register(Tags.compatibility)
def check_async_unsafe(
    app_configs: Sequence[AppConfig] | None, **kwargs: Any
) -> list[CheckMessage]:
    if os.environ.get("DJANGO_ALLOW_ASYNC_UNSAFE") and not settings.DEBUG:
        return [
            Warning(
                "DJANGO_ALLOW_ASYNC_UNSAFE is set.",
                hint=(
                    "Unset this variable to retain Django's async safety guards. "
                    "Unknown synchronous work belongs in a thread; permitting "
                    "database access on the event loop can block other requests."
                ),
                id="aiodrf.W001",
            )
        ]
    return []


@register(Tags.compatibility, deploy=True)
def check_asgi_deployment(
    app_configs: Sequence[AppConfig] | None, **kwargs: Any
) -> list[CheckMessage]:
    """Settings-only hints; configuring ASGI does not prove the serving topology."""
    if not getattr(settings, "ASGI_APPLICATION", None):
        return []
    warnings: list[CheckMessage] = [
        Warning(
            f"ASGI_APPLICATION is configured with persistent connections on {alias!r}.",
            hint="Set CONN_MAX_AGE=0 for async serving; consider backend connection pooling.",
            id="aiodrf.W004",
        )
        for alias, config in connections.settings.items()
        if config.get("CONN_MAX_AGE", 0) != 0
    ]
    for dotted_path in settings.MIDDLEWARE:
        try:
            middleware = import_string(dotted_path)
        except ImportError:
            continue  # Django reports invalid middleware when it loads the handler.
        try:
            async_capable = getattr(middleware, "async_capable", False)
        except ImproperlyConfigured:
            continue  # check_settings reports invalid opt-in configuration.
        if not async_capable:
            warnings.append(
                Warning(
                    f"Middleware {dotted_path!r} does not declare async support.",
                    hint=(
                        "Django adapts it to a thread under ASGI. This can limit long-lived "
                        "stream concurrency; inspect django.request adaptation logs."
                    ),
                    id="aiodrf.W005",
                    obj=dotted_path,
                )
            )
    return warnings


def _url_views(
    base: Any = None,
) -> Iterator[tuple[type[Any], dict[str, Any]]]:
    """
    Each view class the URLconf routes to that subclasses ``base`` (aiodrf's
    ``APIView`` by default), with its ``initkwargs``.
    """
    from django.urls import URLPattern, URLResolver, get_resolver

    if not getattr(settings, "ROOT_URLCONF", None):
        # As Django's own URL checks: nothing to look at.
        return
    if base is None:
        from aiodrf.views import APIView as base

    # Each pattern with the URLconfs that include it: a URLconf including
    # itself is not entered again on that path, while one included under
    # two routes is walked under each.
    root = get_resolver()
    pending: list[tuple[URLPattern | URLResolver, tuple[int, ...]]] = [
        (pattern, (id(root.urlconf_module),)) for pattern in root.url_patterns
    ]
    while pending:
        pattern, ancestors = pending.pop()
        if isinstance(pattern, URLResolver):
            key = id(pattern.urlconf_module)
            if key not in ancestors:
                path = (*ancestors, key)
                pending.extend((child, path) for child in pattern.url_patterns)
            continue
        if not isinstance(pattern, URLPattern):
            continue
        view_class = getattr(pattern.callback, "cls", None)
        if isinstance(view_class, type) and issubclass(view_class, base):
            yield view_class, getattr(pattern.callback, "initkwargs", {})


@register(Tags.urls)
def check_serializer_backends(
    app_configs: Sequence[AppConfig] | None, **kwargs: Any
) -> list[CheckMessage]:
    # ``as_view`` refuses the first view it meets; this lists every one, and
    # also views built before the setting changed.
    errors: list[CheckMessage] = []
    seen = set()
    for view_class, initkwargs in _url_views():
        key = (view_class, repr(sorted(initkwargs.items(), key=lambda item: item[0])))
        if key in seen:
            continue
        seen.add(key)
        try:
            view_class._compile_serializers(initkwargs)
        except ImproperlyConfigured as exc:
            errors.append(Error(str(exc), obj=view_class, id="aiodrf.E005"))
    return errors


@register(Tags.urls)
def check_view_names(
    app_configs: Sequence[AppConfig] | None, **kwargs: Any
) -> list[CheckMessage]:
    # aiodrf routes to DRF's action names, so an adrf-named action is never
    # called and the default action answers instead, silently.
    from aiodrf.contrib import adrf_compat
    from aiodrf.contrib.adrf_compat._names import takes_request
    from aiodrf.contrib.adrf_compat._views import _NAMES, AdrfViewMixin

    if adrf_compat.is_installed():
        return []
    warnings: list[CheckMessage] = []
    seen = set()
    for view_class, _ in _url_views():
        if view_class in seen or issubclass(view_class, AdrfViewMixin):
            continue
        seen.add(view_class)
        # An action's signature: a serializer-shaped ``acreate(validated_data)``
        # on a view is not an action, and the ``perform_*`` hooks never match.
        found = [
            name
            for name in _NAMES
            if callable(method := getattr(view_class, name, None))
            and takes_request(_parameters(method))
        ]
        if found:
            renames = ", ".join(
                f"{view_class.__qualname__}.{n} -> `{_NAMES[n]}`" for n in found
            )
            warnings.append(
                Warning(
                    f"{renames}: aiodrf routes to DRF's action names, so adrf's are never called.",
                    hint=(
                        "Rename them (`python -m aiodrf.codemod <path>` rewrites adrf code), "
                        "or serve adrf's names with AIODRF['ADRF_COMPAT']."
                    ),
                    obj=view_class,
                    id="aiodrf.W006",
                )
            )
    return warnings


def _parameters(function: Any) -> list[str]:
    """The parameter names after ``self``, as ``takes_request`` reads them."""
    try:
        parameters = list(inspect.signature(function).parameters.values())
    except (TypeError, ValueError):
        return []
    prefixes: dict[inspect._ParameterKind, str] = {
        inspect.Parameter.VAR_POSITIONAL: "*",
        inspect.Parameter.VAR_KEYWORD: "**",
    }
    # ``getattr`` on the class returns the plain function: skip ``self``.
    return [prefixes.get(p.kind, "") + p.name for p in parameters[1:]]


# The members DRF calls on a policy, what DRF's base class does in them, and
# aiodrf's base class, whose sync members run the async ones.
_DRF_POLICY_MEMBERS = {
    "has_permission": (
        "returns True, so every request is allowed",
        "permissions.BasePermission",
    ),
    "has_object_permission": (
        "returns True, so every object is allowed",
        "permissions.BasePermission",
    ),
    "authenticate": (
        "raises NotImplementedError on every request",
        "authentication.BaseAuthentication",
    ),
    "allow_request": (
        "raises NotImplementedError on every request",
        "throttling.BaseThrottle",
    ),
}


@register(Tags.urls, Tags.security)
def check_async_only_policies(
    app_configs: Sequence[AppConfig] | None, **kwargs: Any
) -> list[CheckMessage]:
    # aiodrf views await a policy's async member; DRF views call the sync one,
    # which on DRF's base class is DRF's default, not a bridge.
    from rest_framework import authentication, permissions, throttling, views

    from aiodrf.utils import Impl, definer, resolve_pair
    from aiodrf.views import APIView, _permission_leaves

    bases = {
        "authentication_classes": authentication.BaseAuthentication,
        "permission_classes": permissions.BasePermission,
        "throttle_classes": throttling.BaseThrottle,
    }
    # (policy, member) -> (a DRF view using it, the DRF class DRF calls).
    users: dict[tuple[type, str], tuple[type, type]] = {}
    for view_class, initkwargs in _url_views(views.APIView):
        if issubclass(view_class, APIView):
            continue
        for attribute in bases:
            # ``initkwargs`` holds ``as_view()``'s and ``@action``'s policies;
            # the class attributes default to REST_FRAMEWORK's.
            classes = initkwargs.get(attribute, getattr(view_class, attribute))
            for cls in _permission_leaves(classes):
                if not isinstance(cls, type):
                    continue
                for name in _DRF_POLICY_MEMBERS:
                    # DRF's own member: of the base, or of one of DRF's
                    # policies (``AllowAny``, ``IsAuthenticated``) the class
                    # derives from. Neither runs the async member.
                    owner = definer(cls, name)
                    if (
                        owner is not None
                        and owner.__module__.partition(".")[0] == "rest_framework"
                        and resolve_pair(cls, name, f"a{name}") is Impl.ASYNC
                    ):
                        users.setdefault((cls, name), (view_class, owner))
    return [
        Warning(
            f"{cls.__qualname__} defines `a{name}` but not `{name}`, which DRF views "
            f"such as {view_class.__qualname__} call: "
            + (
                f"DRF's default {_DRF_POLICY_MEMBERS[name][0]}."
                if owner in bases.values()
                else f"{owner.__qualname__}.{name} runs, and `a{name}` never does."
            ),
            hint=(
                f"Define `{name}` too, or subclass aiodrf.{_DRF_POLICY_MEMBERS[name][1]}, "
                "whose sync members run the async ones."
            ),
            obj=cls,
            id="aiodrf.W008",
        )
        for (cls, name), (view_class, owner) in users.items()
    ]


# The members DRF calls on a serializer, and aiodrf's async names, which DRF
# never calls. ``get_<field>`` of a method field is found from the fields.
_DRF_SERIALIZER_MEMBERS = frozenset(
    {"validate", "create", "update", "save", "to_representation", "to_internal_value"}
)
_AIODRF_SERIALIZER_MEMBERS = frozenset({f"a{name}" for name in _DRF_SERIALIZER_MEMBERS})


def _async_serializer_members(cls: type) -> tuple[list[str], list[str]]:
    """The coroutine functions of ``cls`` DRF calls, and aiodrf's DRF skips."""
    from rest_framework.serializers import SerializerMethodField

    method_fields = {
        field.method_name or f"get_{name}"
        for name, field in getattr(cls, "_declared_fields", {}).items()
        if isinstance(field, SerializerMethodField)
    }
    called, skipped = [], []
    for name in dir(cls):
        if not inspect.iscoroutinefunction(getattr(cls, name, None)):
            continue
        if (
            name in _DRF_SERIALIZER_MEMBERS
            or name in method_fields
            or name.startswith("validate_")
        ):
            called.append(name)
        elif name in _AIODRF_SERIALIZER_MEMBERS or name.startswith("avalidate_"):
            skipped.append(name)
    return called, skipped


def _nested_serializers(cls: type) -> Iterator[type]:
    """``cls`` and the serializer classes of its declared fields, once each."""
    from rest_framework.serializers import BaseSerializer, ListSerializer

    pending, seen = [cls], set()
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        yield current
        for field in getattr(current, "_declared_fields", {}).values():
            nested = field.child if isinstance(field, ListSerializer) else field
            if isinstance(nested, BaseSerializer):
                pending.append(type(nested))


@register(Tags.urls)
def check_async_serializers_in_drf_views(
    app_configs: Sequence[AppConfig] | None, **kwargs: Any
) -> list[CheckMessage]:
    # aiodrf's views await a DRF serializer's async members; DRF's
    # ``is_valid()``, ``save()`` and ``.data`` call them without awaiting.
    from rest_framework import views
    from rest_framework.serializers import BaseSerializer

    from aiodrf.serializers import AsyncSerializerMixin
    from aiodrf.views import APIView

    # serializer -> the first DRF view using it.
    users: dict[type, type] = {}
    for view_class, initkwargs in _url_views(views.APIView):
        if issubclass(view_class, APIView):
            continue
        serializer_class = initkwargs.get(
            "serializer_class", getattr(view_class, "serializer_class", None)
        )
        if not (
            isinstance(serializer_class, type)
            and issubclass(serializer_class, BaseSerializer)
        ):
            continue
        for cls in _nested_serializers(serializer_class):
            if not issubclass(cls, AsyncSerializerMixin):
                users.setdefault(cls, view_class)
    warnings: list[CheckMessage] = []
    for cls, view_class in users.items():
        called, skipped = _async_serializer_members(cls)
        problems = []
        if called:
            problems.append(
                f"DRF calls {_names(called)} without awaiting, so a coroutine "
                "ends up in `validated_data`, the saved row or `.data`"
            )
        if skipped:
            problems.append(f"DRF never calls {_names(skipped)}")
        if problems:
            warnings.append(
                Warning(
                    f"{cls.__qualname__} is a DRF serializer with async members, used "
                    f"by the DRF view {view_class.__qualname__}: "
                    + "; ".join(problems)
                    + ".",
                    hint=(
                        "Inherit from aiodrf.serializers, whose sync members run the "
                        "async ones, or serve the view with aiodrf's views."
                    ),
                    obj=cls,
                    id="aiodrf.W010",
                )
            )
    return warnings


def _names(names: list[str]) -> str:
    return ", ".join(f"`{name}`" for name in names)
