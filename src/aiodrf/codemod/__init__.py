"""
Rewrite DRF and adrf code to aiodrf::

    python -m aiodrf.codemod path/to/app            # rewrite in place
    python -m aiodrf.codemod --diff path/to/app     # show what would change

See :mod:`aiodrf.codemod.transform` for the exact rules. It needs libcst:
``pip install django-aiodrf[codemod]``.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aiodrf.codemod.transform import AioDRFTransformer, transform_source

__all__ = ["AioDRFTransformer", "transform_source"]

# libcst is imported with the transformer, on first use, so that the command
# can say what to install when it is missing.
INSTALL = "aiodrf.codemod needs libcst: pip install django-aiodrf[codemod]"


def __getattr__(name: str) -> Any:
    if name in __all__:
        from aiodrf.codemod import transform

        return getattr(transform, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
