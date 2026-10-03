"""
List serializers whose child refers to them weakly.

DRF binds a list serializer's child to it (``child.parent``) and the list
holds the child: a reference cycle. ``many_init`` also gives the child the
list's ``instance``, so every object of a page (and its prefetched relations)
stays alive until the cyclic garbage collector runs, which under concurrency
scans every request in flight. Here the child's ``parent`` is a weak proxy of
the list: while the list exists the child reads it as before (``root``,
``context``, ``parent.instance``), and once nothing else refers to the list,
reference counting frees it with its instances.

Use them per serializer (``Meta.list_serializer_class``) or for a project's
base serializer (``default_list_serializer_class``)::

    from aiodrf import serializers
    from aiodrf.contrib.list_serializers import ListSerializer

    class ModelSerializer(serializers.ModelSerializer):
        default_list_serializer_class = ListSerializer

``aiodrf.contrib.monkeypatches`` (``weak_list_children``) does the same for
every DRF list serializer of the process. A child kept after its list is gone
(``serializer.child`` stored on its own) raises ``ReferenceError`` when it
reads ``parent``; so does ``child.parent is serializer``, which is False for
the proxy. Compare with ``==``.
"""

# django-fastdrf's, which maintains them.
from fastdrf.list_serializers import WeakChildMixin

from aiodrf import serializers
from aiodrf.contrib.typed import SchemaListSerializer as _SchemaListSerializer

__all__ = ["ListSerializer", "SchemaListSerializer"]


class ListSerializer(WeakChildMixin, serializers.ListSerializer):
    """aiodrf's ``ListSerializer`` with a weakly bound child."""


class SchemaListSerializer(WeakChildMixin, _SchemaListSerializer):
    """The list serializer of msgspec and pydantic serializers, weakly bound."""
