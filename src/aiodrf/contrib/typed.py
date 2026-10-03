"""
Serializers backed by typed schema classes (msgspec ``Struct``, pydantic
``BaseModel``).

A schema serializer participates in DRF's serializer lifecycle: generic views,
``many=True``, output field introspection and drf-spectacular integration.
Validation and representation use the schema library's rules rather than DRF
field coercions. The OpenAPI schema is generated from the same class::

    class BookIn(msgspec.Struct):
        title: str
        pages: int = 100

    class BookSerializer(MsgspecSerializer):
        class Meta:
            schema = BookIn

Different classes can be used for input and output with ``Meta.input_schema``
and ``Meta.output_schema``. A bare schema class can also be used directly as
a view's ``serializer_class``; aiodrf wraps it with :func:`adapt`.

The schema serializers are django-fastdrf's (:mod:`fastdrf.typed`), which
maintains them, on aiodrf's asynchronous serializer bases: ``ais_valid()``,
``adata()`` and ``asave()`` work as for any aiodrf serializer.
"""

from typing import Any, ClassVar

from django.core.exceptions import ImproperlyConfigured
from fastdrf import typed as _fastdrf_typed

from aiodrf import serializers
from aiodrf.utils import bridge_base, call_pair, run_sync, user_defines

__all__ = [
    "SchemaListSerializer",
    "SchemaSerializer",
    "SchemaViewMixin",
    "adapt",
    "schema_serializer",
]


@bridge_base
class SchemaListSerializer(
    _fastdrf_typed.SchemaListSerializer, serializers.ListSerializer
):
    """django-fastdrf's schema list serializer on aiodrf's list serializer."""

    # Defined here, on a bridge base, so that aiodrf reads it as the
    # framework's: a child's async representation is then awaited.
    def to_representation(self, data: Any) -> Any:
        return _fastdrf_typed.SchemaListSerializer.to_representation(self, data)


class SchemaSerializer(_fastdrf_typed.SchemaSerializer, serializers.Serializer):
    """Base class of ``MsgspecSerializer`` and ``PydanticSerializer``."""

    #: ``"msgspec"`` or ``"pydantic"`` (``FASTDRF["ALLOWED_SERIALIZER_BACKENDS"]``).
    schema_library: ClassVar[str]
    default_list_serializer_class = SchemaListSerializer  # type: ignore[misc]
    # aiodrf's, which builds ``default_list_serializer_class``; django-fastdrf's
    # class (first in the MRO) has its own.
    many_init: Any = classmethod(serializers.BaseSerializer.many_init.__func__)  # type: ignore[attr-defined]


# The serializer classes of bare schema classes: those of
# ``aiodrf.contrib.msgspec`` and ``aiodrf.contrib.pydantic``, one per class (or
# per input, output and model).
_classes = _fastdrf_typed._SchemaClasses("aiodrf.contrib")
_adapted = _classes.cache
adapt = _classes.adapt
schema_serializer = _classes.schema_serializer


# A comment, not a docstring: drf-spectacular would publish it as the
# description of every view without one of its own.
#
# Use msgspec Structs or pydantic models as a view's serializer::
#
#     class BookViewSet(SchemaViewMixin, viewsets.ModelViewSet):
#         queryset = Book.objects.all()
#         input_schema = BookIn  # validates request bodies
#         output_schema = BookOut  # represents responses
#
# Either schema alone does both. The pair becomes one serializer class
# when the URL is built; in a generic view it writes ``queryset.model``
# and ``serializer.validated_object`` is the ``input_schema`` instance.
# In any view, ``aget_validated_body()`` returns the request body as an
# ``input_schema`` instance and ``schema_response()`` represents data with
# ``output_schema``.
class SchemaViewMixin:
    input_schema: type | None = None
    output_schema: type | None = None
    # Set by the view this is mixed into.
    request: Any
    format_kwarg: Any

    @classmethod
    def _compile_serializers(cls, initkwargs: dict[str, Any]) -> None:
        # The checks of the other bases run too (a native view's).
        super()._compile_serializers(initkwargs)  # type: ignore[misc]
        input_schema = initkwargs.get("input_schema", cls.input_schema)
        output_schema = initkwargs.get("output_schema", cls.output_schema)
        if input_schema is None and output_schema is None:
            raise ImproperlyConfigured(
                f"{cls.__module__}.{cls.__qualname__} uses SchemaViewMixin and needs "
                "`input_schema`, `output_schema` or both."
            )
        queryset = initkwargs.get("queryset", getattr(cls, "queryset", None))
        _schema_serializer_for(cls, input_schema, output_schema, queryset)

    def get_serializer_class(self) -> type[SchemaSerializer]:
        # The model is the view's ``queryset`` attribute, given to the class
        # or to ``as_view()``; a ``get_queryset()`` override does not change it.
        queryset = getattr(self, "queryset", None)
        return _schema_serializer_for(
            type(self), self.input_schema, self.output_schema, queryset
        )

    def get_validated_body(self, *, partial: bool = False) -> Any:
        """The request body as an ``input_schema`` instance; DRF's 400 if invalid."""
        serializer = self._schema_serializer(data=self.request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        return serializer.validated_object

    async def aget_validated_body(self, *, partial: bool = False) -> Any:
        """Async counterpart of ``get_validated_body``."""
        from aiodrf import aio

        data = await self.request.adata()
        if hasattr(self, "_awaits_serializer") and self._awaits_serializer():
            serializer = await call_pair(
                self, "get_serializer", "aget_serializer", data=data, partial=partial
            )
        elif user_defines(self, *_SERIALIZER_FACTORY):
            # The project's factory hooks may query: build it in the worker.
            serializer = await run_sync(self._schema_serializer)(
                data=data, partial=partial
            )
        else:
            serializer = self._schema_serializer(data=data, partial=partial)
        await aio.is_valid(serializer, raise_exception=True)
        return serializer.validated_object

    def schema_response(self, data: Any, *, many: bool = False, **kwargs: Any) -> Any:
        """
        A ``Response`` with ``data`` represented by ``output_schema``;
        ``kwargs`` are ``Response``'s (``status``, ``headers``). Model
        instances are read by attribute, so relations must be loaded.
        """
        from aiodrf.response import Response

        serializer = self._schema_serializer(data, many=many)
        return Response(serializer.to_representation(data), **kwargs)

    def _schema_serializer(self, *args: Any, **kwargs: Any) -> SchemaSerializer:
        context = (
            self.get_serializer_context()
            if hasattr(self, "get_serializer_context")
            else {"request": self.request, "format": self.format_kwarg, "view": self}
        )
        return self.get_serializer_class()(*args, context=context, **kwargs)


_SERIALIZER_FACTORY = (
    "get_serializer_context",
    "get_serializer_class",
    "get_serializer",
)


def _schema_serializer_for(
    view_class: type, input_schema: Any, output_schema: Any, queryset: Any
) -> type[SchemaSerializer]:
    from fastdrf.typed import require_allowed

    input_schema = input_schema or output_schema
    output_schema = output_schema or input_schema
    for schema in {input_schema, output_schema}:
        require_allowed(schema, view_class)
    # The class ``aiodrf.contrib.<library>.serializers`` builds: aiodrf's.
    return schema_serializer(  # type: ignore[return-value]
        input_schema, output_schema, getattr(queryset, "model", None)
    )
