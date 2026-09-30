"""
adrf's ``fields``: DRF's fields, and adrf's hook for custom ones.

A field class derived from one of these may define
``async def ato_representation(self, value)``, as in adrf; aiodrf awaits it
when it represents the field (``None`` stays ``None`` without calling it).
Fields that do not define it are represented as DRF's, in the page's hop.
``SerializerMethodField`` is DRF's: aiodrf awaits ``async def get_<field>``.
"""

from typing import TYPE_CHECKING, Any

from rest_framework import serializers
from rest_framework.serializers import SerializerMethodField

from aiodrf.compat import BigIntegerField as _DRFBigIntegerField
from aiodrf.utils import definer, is_async_callable

__all__ = [
    "AsyncFieldMixin",
    "BooleanField",
    "CharField",
    "ChoiceField",
    "DateField",
    "DateTimeField",
    "DecimalField",
    "DictField",
    "DurationField",
    "EmailField",
    "Field",
    "FileField",
    "FilePathField",
    "FloatField",
    "HStoreField",
    "HiddenField",
    "IPAddressField",
    "ImageField",
    "IntegerField",
    "JSONField",
    "ListField",
    "ModelField",
    "MultipleChoiceField",
    "ReadOnlyField",
    "RegexField",
    "SerializerMethodField",
    "SlugField",
    "TimeField",
    "URLField",
    "UUIDField",
]


if TYPE_CHECKING:
    _Field = serializers.Field
else:
    _Field = object


class AsyncFieldMixin(_Field):
    """adrf's base of its fields."""

    #: Read by aiodrf's representation: the class awaits ``ato_representation``.
    _aiodrf_async_field = False

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        cls._aiodrf_async_field = definer(
            cls, "ato_representation"
        ) is not AsyncFieldMixin and (is_async_callable(cls.ato_representation))

    async def ato_representation(self, value: Any) -> Any:
        return self.to_representation(value)


if _DRFBigIntegerField is not None:

    class BigIntegerField(AsyncFieldMixin, serializers.BigIntegerField):
        pass

    __all__ += ["BigIntegerField"]


class BooleanField(AsyncFieldMixin, serializers.BooleanField):
    pass


class CharField(AsyncFieldMixin, serializers.CharField):
    pass


class ChoiceField(AsyncFieldMixin, serializers.ChoiceField):
    pass


class DateField(AsyncFieldMixin, serializers.DateField):
    pass


class DateTimeField(AsyncFieldMixin, serializers.DateTimeField):
    pass


class DecimalField(AsyncFieldMixin, serializers.DecimalField):
    pass


class DictField(AsyncFieldMixin, serializers.DictField):
    pass


class DurationField(AsyncFieldMixin, serializers.DurationField):
    pass


class EmailField(AsyncFieldMixin, serializers.EmailField):
    pass


class Field(AsyncFieldMixin, serializers.Field):
    pass


class FileField(AsyncFieldMixin, serializers.FileField):
    pass


class FilePathField(AsyncFieldMixin, serializers.FilePathField):
    pass


class FloatField(AsyncFieldMixin, serializers.FloatField):
    pass


class HiddenField(AsyncFieldMixin, serializers.HiddenField):
    pass


class HStoreField(AsyncFieldMixin, serializers.HStoreField):
    pass


class IPAddressField(AsyncFieldMixin, serializers.IPAddressField):
    pass


class ImageField(AsyncFieldMixin, serializers.ImageField):
    pass


class IntegerField(AsyncFieldMixin, serializers.IntegerField):
    pass


class JSONField(AsyncFieldMixin, serializers.JSONField):
    pass


class ListField(AsyncFieldMixin, serializers.ListField):
    pass


class ModelField(AsyncFieldMixin, serializers.ModelField):
    pass


class MultipleChoiceField(AsyncFieldMixin, serializers.MultipleChoiceField):
    pass


class ReadOnlyField(AsyncFieldMixin, serializers.ReadOnlyField):
    pass


class RegexField(AsyncFieldMixin, serializers.RegexField):
    pass


class SlugField(AsyncFieldMixin, serializers.SlugField):
    pass


class TimeField(AsyncFieldMixin, serializers.TimeField):
    pass


class URLField(AsyncFieldMixin, serializers.URLField):
    pass


class UUIDField(AsyncFieldMixin, serializers.UUIDField):
    pass
