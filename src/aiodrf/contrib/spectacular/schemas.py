"""OpenAPI schema generation for explicit input and output declarations."""

from typing import Any

from drf_spectacular import openapi

from aiodrf.utils import user_defines
from aiodrf.views import APIView

__all__ = ["AutoSchema"]


class AutoSchema(openapi.AutoSchema):
    """
    drf-spectacular's ``AutoSchema``, which also documents the fields of a
    view's query serializer (``get_query_serializer_class()``) as query
    parameters. Opt in with ``REST_FRAMEWORK["DEFAULT_SCHEMA_CLASS"]``;
    ``@extend_schema(parameters=[...])`` adds to them as usual.
    """

    def get_override_parameters(self) -> list[Any]:
        parameters: list[Any] = list(super().get_override_parameters())
        view = self.view
        if isinstance(view, APIView) and (
            view.query_serializer_class is not None
            or user_defines(view, "get_query_serializer_class")
        ):
            # An override may return None for the actions without one.
            serializer_class = view.get_query_serializer_class()
            if serializer_class is not None:
                parameters.append(serializer_class)
        return parameters
