"""
adrf's ``routers``: DRF's, routing an async viewset's actions to adrf's
names (``list`` to ``alist``, ...), so ``self.action`` is what adrf code
compares with.
"""

from collections.abc import Mapping
from typing import Any

from aiodrf import routers

__all__ = ["DefaultRouter", "SimpleRouter"]


class _AsyncActionsMixin:
    sync_to_async_action_map = {
        "list": "alist",
        "create": "acreate",
        "retrieve": "aretrieve",
        "update": "aupdate",
        "destroy": "adestroy",
        "partial_update": "partial_aupdate",
    }

    def get_method_map(
        self, viewset: type[Any], method_map: Mapping[str, str]
    ) -> dict[str, str]:
        # adrf's, choosing per action the name the viewset implements: its
        # adrf name, unless the DRF name is defined nearer (an own ``list``
        # over an inherited ``alist``) or is the only one.
        routed = {}
        for method, action in method_map.items():
            name = _implemented(
                viewset, self.sync_to_async_action_map.get(action, action), action
            )
            if name is not None:
                routed[method] = name
        return routed


def _implemented(viewset: type[Any], async_name: str, sync_name: str) -> str | None:
    for klass in viewset.__mro__:
        for name in (async_name, sync_name):
            if name in vars(klass):
                return name
    return None


class SimpleRouter(_AsyncActionsMixin, routers.SimpleRouter):
    pass


class DefaultRouter(_AsyncActionsMixin, routers.DefaultRouter):
    pass
