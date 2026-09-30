"""adrf's ``utils``."""

from collections.abc import Callable, Container
from typing import Any

__all__ = ["getmembers"]


def getmembers(
    object: Any,
    predicate: Callable[[Any], Any] | None = None,
    exclude_names: Container[str] = (),
) -> list[tuple[str, Any]]:
    """``inspect.getmembers()`` that skips ``exclude_names`` without reading them."""
    results = []
    for name in dir(object):
        if name in exclude_names:
            continue
        try:
            value = getattr(object, name)
        except AttributeError:
            continue
        if predicate is None or predicate(value):
            results.append((name, value))
    return sorted(results, key=lambda pair: pair[0])
