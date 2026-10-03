"""Django application registration and startup hooks."""

from contextlib import AbstractContextManager
from typing import Any

from django.apps import AppConfig
from django.db import connections, transaction


def atomic(using: str) -> AbstractContextManager[Any]:
    """The transaction ``ATOMIC_SAVE`` enters on a MongoDB database."""
    from django_mongodb_backend import transaction as mongodb_transaction

    # The backend's own answer, from the server's ``hello``: a replica set or
    # a sharded cluster. A standalone server refuses a transaction. Private
    # to django-mongodb-backend; tests/ecosystem/mongodb pins it.
    features: Any = connections[using].features
    if features._supports_transactions:
        return mongodb_transaction.atomic(using)
    return transaction.atomic(using=using)


class MongoDBConfig(AppConfig):
    name = "aiodrf.contrib.mongodb"
    label = "aiodrf_mongodb"
    verbose_name = "aiodrf: MongoDB"

    def ready(self) -> None:
        from aiodrf.aio._save import _ATOMIC_FACTORIES

        _ATOMIC_FACTORIES["mongodb"] = atomic
        _register_compiled_fields()


def _register_compiled_fields() -> None:
    """
    What ``SERIALIZER_BACKEND`` compiles of these packages: ``ObjectId``
    columns read by Django's descriptor, django-mongodb-extensions'
    ``ObjectIdField`` (``str(value)``) and ``ObjectIdPrimaryKeyRelatedField``.
    The same functions each time: registering again changes nothing.
    """
    from django_mongodb_backend import fields as mongodb_fields
    from fastdrf.registry import (
        register_field,
        register_key_field,
        register_model_field,
    )

    from aiodrf.contrib.mongodb.fields import ObjectIdPrimaryKeyRelatedField

    register_model_field(mongodb_fields.ObjectIdAutoField)
    register_model_field(mongodb_fields.ObjectIdField)
    register_key_field(ObjectIdPrimaryKeyRelatedField, representation=_object_id_keys)
    try:
        from django_mongodb_extensions.rest_framework import ObjectIdField
    except ImportError:
        return
    register_field(ObjectIdField, representation=_strings)


def _object_id_key(value: object) -> object:
    # ObjectIdPrimaryKeyRelatedField.to_representation of a related key.
    from bson import ObjectId

    return str(value) if isinstance(value, ObjectId) else value


def _object_id_keys(field: object) -> Any:
    return _object_id_key


def _strings(field: object) -> Any:
    return str
