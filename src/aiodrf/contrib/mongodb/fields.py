"""Serializer fields for models on django-mongodb-backend."""

from typing import Any

from bson import ObjectId
from rest_framework import relations

__all__ = ["ObjectIdPrimaryKeyRelatedField"]


class ObjectIdPrimaryKeyRelatedField(relations.PrimaryKeyRelatedField):
    """
    ``PrimaryKeyRelatedField`` that represents an ``ObjectId`` key as its
    24-character hex string, which JSON can encode; DRF's returns the
    ``ObjectId`` and its JSON encoder refuses it. Other keys are unchanged.

    Make it the field of every relation of a ``ModelSerializer``
    (django-mongodb-extensions' ``MongoModelSerializer`` too)::

        class BookSerializer(MongoModelSerializer):
            serializer_related_field = ObjectIdPrimaryKeyRelatedField

    Input is DRF's: the backend accepts the string for the key.
    """

    def to_representation(self, value: Any) -> Any:
        pk = super().to_representation(value)
        return str(pk) if isinstance(pk, ObjectId) else pk
