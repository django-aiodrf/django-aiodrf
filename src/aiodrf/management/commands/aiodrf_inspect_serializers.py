"""
``manage.py aiodrf_inspect_serializers``: django-fastdrf's
``fastdrf_inspect_serializers``, which maintains it, for aiodrf's views.

What differs is how a view declares its serializer: ``aget_serializer_class``
may choose it too, a bare schema class is adapted to aiodrf's schema
serializers, and a serializer on DRF's classes is compiled by aiodrf as on
its own.
"""

from typing import Any

from fastdrf.management.commands import fastdrf_inspect_serializers
from rest_framework.serializers import BaseSerializer

from aiodrf.utils import definer, user_defines


class Command(fastdrf_inspect_serializers.Command):
    def _declared_serializer(self, view_class: Any, initkwargs: dict[str, Any]) -> Any:
        return _declared_serializer(view_class, initkwargs)

    def _chooser(self, view_class: type) -> str | None:
        # Either member of the pair may choose the serializer.
        return next(
            (
                name
                for name in ("get_serializer_class", "aget_serializer_class")
                if _chooses_serializer(view_class, name)
            ),
            None,
        )

    def _on_drf_bases(self, serializer: Any, directions: Any) -> Any:
        # aiodrf compiles serializers on DRF's classes as on its own.
        return directions


def _is_schema_view(view_class: Any) -> bool:
    from aiodrf.contrib.typed import SchemaViewMixin

    return isinstance(view_class, type) and issubclass(view_class, SchemaViewMixin)


def _declared_serializer(view_class: Any, initkwargs: dict[str, Any]) -> Any:
    """
    The serializer class a view declares, as the view will build it: a
    SchemaViewMixin's schema pair, or a bare schema class adapted as
    GenericAPIView adapts it. Raises ImproperlyConfigured when the view
    may not use it.
    """
    from aiodrf.contrib.typed import _schema_serializer_for, adapt

    if _is_schema_view(view_class):
        input_schema = initkwargs.get("input_schema", view_class.input_schema)
        output_schema = initkwargs.get("output_schema", view_class.output_schema)
        if input_schema is not None or output_schema is not None:
            queryset = initkwargs.get("queryset", getattr(view_class, "queryset", None))
            return _schema_serializer_for(
                view_class, input_schema, output_schema, queryset
            )
    serializer_class = initkwargs.get(
        "serializer_class", getattr(view_class, "serializer_class", None)
    )
    if serializer_class is None or (
        isinstance(serializer_class, type)
        and issubclass(serializer_class, BaseSerializer)
    ):
        return serializer_class
    return adapt(serializer_class)


def _chooses_serializer(view_class: type, name: str) -> bool:
    # SchemaViewMixin's get_serializer_class returns the declared pair.
    if _is_schema_view(view_class):
        from aiodrf.contrib.typed import SchemaViewMixin

        return definer(view_class, name) not in (None, SchemaViewMixin) and (
            user_defines(view_class, name)
        )
    return bool(user_defines(view_class, name))
