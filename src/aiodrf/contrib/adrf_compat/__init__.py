"""
Run a project written for adrf on aiodrf without changing its code (opt-in).

With ``AIODRF = {"ADRF_COMPAT": True}``, or ``install()`` called before
anything imports adrf, the modules ``adrf``, ``adrf.views``,
``adrf.viewsets``, ... are served by aiodrf: ``sys.modules`` gets aliases to
the modules of ``aiodrf.contrib.adrf_compat.adrf``, and adrf's own code never
runs. adrf must not be installed.

adrf's names and signatures keep working: the actions ``alist``,
``acreate``, ``aretrieve``, ``aupdate``, ``partial_aupdate``, ``adestroy``,
the hooks ``perform_acreate``, ``perform_aupdate``, ``perform_adestroy``,
``get_apaginated_response``, ``check_async_permissions`` and its siblings,
adrf's routers, fields with ``async def ato_representation``, ``await
serializer.adata``, ``AsyncRequest``. What runs is aiodrf: permissions are
asked in DRF's order, a page is represented in one thread hop.

Each adrf module imported and each adrf method name defined warns once with
``AdrfCompatWarning``; the layer itself stays supported. See
``docs/guides/migration-from-adrf.md``.
"""

import importlib
import importlib.abc
import importlib.machinery
import importlib.metadata
import importlib.util
import os
import sys
import warnings
from collections.abc import Sequence
from types import ModuleType

from django.core.exceptions import ImproperlyConfigured

__all__ = ["AdrfCompatWarning", "install", "is_installed", "uninstall"]


class AdrfCompatWarning(DeprecationWarning):
    """An adrf name is in use; aiodrf's own name is preferred."""


# What each adrf module is, in aiodrf.
_REPLACEMENTS = {
    "adrf": "aiodrf",
    "adrf.decorators": "aiodrf.decorators",
    "adrf.fields": "rest_framework.fields",
    "adrf.generics": "aiodrf.generics",
    "adrf.mixins": "aiodrf.mixins",
    "adrf.permissions": "aiodrf.permissions",
    "adrf.requests": "aiodrf.request",
    "adrf.routers": "aiodrf.routers",
    "adrf.serializers": "aiodrf.serializers",
    "adrf.shortcuts": "django.shortcuts",
    "adrf.test": "aiodrf.test",
    "adrf.utils": "inspect",
    "adrf.views": "aiodrf.views",
    "adrf.viewsets": "aiodrf.viewsets",
}
_SHIMS = __name__ + ".adrf"
# The frames a warning skips to reach the project's import or class.
_SKIPPED = (
    "<frozen importlib",
    os.path.dirname(importlib.__file__),
    os.path.dirname(__file__),
)
_warned: set[str] = set()


def warn_once(name: str, replacement: str) -> None:
    if name in _warned:
        return
    warnings.warn(
        f"{name} is adrf's, served by aiodrf's adrf compatibility layer; aiodrf's is "
        f"{replacement}. `python -m aiodrf.codemod --diff <path>` rewrites adrf code.",
        AdrfCompatWarning,
        # Point at the project's code, not at the import system or this layer.
        skip_file_prefixes=_SKIPPED,
    )
    # Only once it was warned: a warning turned into an error fails again.
    _warned.add(name)


class _Aliases(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Serves ``adrf`` and its modules as the shim modules, without copying them."""

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None = None,
        target: ModuleType | None = None,
    ) -> importlib.machinery.ModuleSpec | None:
        if fullname not in _REPLACEMENTS:
            return None
        return importlib.machinery.ModuleSpec(
            fullname, self, is_package=fullname == "adrf"
        )

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> ModuleType | None:
        warn_once(spec.name, _REPLACEMENTS[spec.name])
        if spec.name == "adrf":
            # A package of its own: the shim package holds the shim modules
            # the shims import from each other, and ``from adrf import x``
            # would find them there without importing ``adrf.x``.
            return None
        return importlib.import_module(_SHIMS + spec.name.removeprefix("adrf"))

    def exec_module(self, module: ModuleType) -> None:
        # A shim module is already executed; the import system only records
        # it under the adrf name.
        if module.__name__ == "adrf":
            module.__doc__ = importlib.import_module(_SHIMS).__doc__


_finder = _Aliases()


def is_installed() -> bool:
    return _finder in sys.meta_path


def install() -> None:
    """Serve ``adrf`` from aiodrf. Idempotent; call it before adrf is imported."""
    if is_installed():
        return
    try:
        importlib.metadata.distribution("adrf")
    except importlib.metadata.PackageNotFoundError:
        pass
    else:
        raise ImproperlyConfigured(
            "AIODRF['ADRF_COMPAT'] serves the adrf modules from aiodrf, but adrf is "
            "installed. Uninstall adrf (`pip uninstall adrf`)."
        )
    if "adrf" in sys.modules:
        raise ImproperlyConfigured(
            "adrf was imported before aiodrf's adrf compatibility layer was installed. "
            "Put 'aiodrf' before the apps that import adrf in INSTALLED_APPS, or call "
            "aiodrf.contrib.adrf_compat.install() earlier (settings.py)."
        )
    sys.meta_path.insert(0, _finder)


def uninstall() -> None:
    """Remove the aliases (for tests). Classes already built from them stay."""
    if is_installed():
        sys.meta_path.remove(_finder)
    for name in _REPLACEMENTS:
        # Only the aliases: the shim module, or this layer's package, under
        # the adrf name.
        module = sys.modules.get(name)
        shim = sys.modules.get(_SHIMS + name.removeprefix("adrf"))
        if module is not None and (
            module is shim or getattr(module.__spec__, "loader", None) is _finder
        ):
            del sys.modules[name]
