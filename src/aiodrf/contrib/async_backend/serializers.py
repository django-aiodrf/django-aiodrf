"""
``ModelSerializer`` whose ``create`` and ``update`` write natively.

``acreate`` and ``aupdate`` follow DRF's ``ModelSerializer.create`` and
``update``: the row is written with the model's ``async_objects`` /
``async_save``, and to-many fields are set through their auto-created
through model, as Django's ``ManyRelatedManager.set()`` does (same
``m2m_changed`` signals, symmetrical relations included). DRF writes only
to-many fields with auto-created through models. With
``AIODRF["ATOMIC_SAVE"]`` the write runs in django-async-backend's
``async_atomic``.

Signal receivers (``post_save``, ``m2m_changed``) are Django's: synchronous
ones run in a thread with Django's connection, in a transaction of Django's
that ends after the native one (``receivers_in_transaction``), so what they
defer with ``on_commit()`` waits for the native commit.
"""

import contextlib
import traceback
from collections.abc import AsyncGenerator
from typing import Any

from django.db.models import QuerySet
from django.db.models.signals import m2m_changed
from rest_framework.serializers import raise_errors_on_nested_writes
from rest_framework.utils import model_meta

from aiodrf import serializers
from aiodrf.contrib.async_backend._package import (
    NativeQuerySet,
    async_atomic,
    awrite_alias,
    receivers_in_transaction,
    run_native,
)
from aiodrf.settings import aiodrf_settings
from aiodrf.utils import run_sync

__all__ = ["ModelSerializer", "aset_many"]


class ModelSerializer(serializers.ModelSerializer):
    """DRF's ``ModelSerializer`` with native ``acreate`` / ``aupdate``."""

    async def acreate(self, validated_data: dict[str, Any]) -> Any:
        raise_errors_on_nested_writes("create", self, validated_data)

        ModelClass = self.Meta.model

        # Remove many-to-many relationships from validated_data.
        # They are not valid arguments to the default `.create()` method,
        # as they require that the instance has already been saved.
        info = model_meta.get_field_info(ModelClass)
        many_to_many = {}
        for field_name, relation_info in info.relations.items():
            if relation_info.to_many and (field_name in validated_data):
                many_to_many[field_name] = validated_data.pop(field_name)

        # Where Model.objects.create() writes, as the routers say (asked off
        # the loop); passed on, so django-async-backend does not ask again.
        using = await awrite_alias(ModelClass)
        async with _atomic_save(using):
            try:
                # ``async_objects``: added to Model by django-async-backend.
                instance = await ModelClass.async_objects.using(using).acreate(  # type: ignore[attr-defined]
                    **validated_data
                )
            except TypeError as exc:
                raise TypeError(_create_type_error(self, ModelClass, exc)) from exc

            # Save many-to-many relationships after the instance is created.
            for field_name, value in many_to_many.items():
                await aset_many(instance, field_name, value)

        return instance

    async def aupdate(self, instance: Any, validated_data: dict[str, Any]) -> Any:
        raise_errors_on_nested_writes("update", self, validated_data)
        info = model_meta.get_field_info(instance)

        # Simply set each attribute on the instance, and then save it.
        # Note that unlike `.create()` we don't need to treat many-to-many
        # relationships as being a special case. During updates we already
        # have an instance pk for the relationships to be associated with.
        m2m_fields = []
        for attr, value in validated_data.items():
            if attr in info.relations and info.relations[attr].to_many:
                m2m_fields.append((attr, value))
            else:
                setattr(instance, attr, value)

        # Model.save() routes with the instance as a hint.
        using = await awrite_alias(type(instance), instance=instance)
        async with _atomic_save(using):
            await instance.async_save(using=using)

            # Note that many-to-many fields are set after updating instance.
            # Setting m2m fields triggers signals which could potentially change
            # updated instance and we do not want it to collide with .update()
            for attr, value in m2m_fields:
                await aset_many(instance, attr, value)

        return instance

    # Synchronous callers (the browsable API, a synchronous hook calling
    # ``serializer.save()``) get the same writes on a connection of their own.

    def create(self, validated_data: Any) -> Any:
        return run_native(self.acreate, validated_data)

    def update(self, instance: Any, validated_data: Any) -> Any:
        return run_native(self.aupdate, instance, validated_data)


@contextlib.asynccontextmanager
async def _atomic_save(using: str) -> AsyncGenerator[None]:
    if not aiodrf_settings.ATOMIC_SAVE:
        yield
        return
    async with receivers_in_transaction(using), async_atomic(using=using):
        yield


def _create_type_error(serializer: Any, model_class: Any, exc: TypeError) -> str:
    # DRF's message for a ``TypeError`` from ``Model.objects.create()``.
    tb = traceback.format_exc()
    return (
        "Got a `TypeError` when calling `%s.%s.create()`. "
        "This may be because you have a writable field on the "
        "serializer class that is not a valid argument to "
        "`%s.%s.create()`. You may need to make the field "
        "read-only, or override the %s.create() method to handle "
        "this correctly.\nOriginal exception was:\n %s"
        % (
            model_class.__name__,
            "async_objects",
            model_class.__name__,
            "async_objects",
            type(serializer).__name__,
            tb,
        )
    )


# -- Many-to-many ---------------------------------------------------------------------


async def aset_many(instance: Any, field_name: str, objs: Any) -> None:
    """
    ``getattr(instance, field_name).set(objs)``, written natively: Django's
    ``ManyRelatedManager.set_base`` with ``clear=False``.

    The related manager is Django's (building it runs no query); only its
    reads and writes of the through table go through the through model's
    ``async_objects``. When the related model's default manager filters, the
    ids already set are read through it with Django's connection, as
    Django does, outside the native transaction.
    """
    manager = getattr(instance, field_name)
    # Force evaluation of `objs` in case it's a queryset whose value
    # could be affected by `manager.clear()`. Refs #19816. A Django queryset
    # queries on Django's connection, in the worker.
    if isinstance(objs, QuerySet):
        objs = await run_sync(tuple)(objs)
    elif isinstance(objs, NativeQuerySet):
        objs = tuple([obj async for obj in objs])
    else:
        objs = tuple(objs)
    db = await awrite_alias(manager.through, instance=instance)
    async with receivers_in_transaction(db), async_atomic(using=db, savepoint=False):
        manager._remove_prefetched_objects()
        old_ids = await _aold_ids(manager, db)
        new_objs = []
        for obj in objs:
            fk_val = (
                manager.target_field.get_foreign_related_value(obj)[0]
                if isinstance(obj, manager.model)
                else manager.target_field.get_prep_value(obj)
            )
            if fk_val in old_ids:
                old_ids.remove(fk_val)
            else:
                new_objs.append(obj)

        await _aremove_items(manager, db, old_ids)
        await _aadd_items(
            manager, db, manager.source_field_name, manager.target_field_name, new_objs
        )
        # If this is a symmetrical m2m relation to self, add the mirror
        # entry in the m2m table.
        if manager.symmetrical:
            await _aadd_items(
                manager,
                db,
                manager.target_field_name,
                manager.source_field_name,
                new_objs,
            )


async def _aold_ids(manager: Any, db: str) -> set[Any]:
    # Django reads them through the related manager, so rows the related
    # model's default manager hides are neither counted nor removed.
    attname = manager.target_field.target_field.attname
    if manager.model._default_manager.get_queryset()._has_filters():
        # A native queryset cannot apply that manager's filters: Django's
        # connection reads them, outside the native transaction.
        return await run_sync(
            lambda: set(manager.using(db).values_list(attname, flat=True))
        )()
    return {
        value
        async for value in manager.through.async_objects.using(db)
        .filter(**{manager.source_field_name: manager.related_val[0]})
        .values_list(f"{manager.target_field_name}_id", flat=True)
    }


async def _aadd_items(
    manager: Any, db: str, source_field_name: str, target_field_name: str, objs: Any
) -> None:
    # Django's ``ManyRelatedManager._add_items``.
    if not objs:
        return
    through = manager.through
    target_ids = manager._get_target_ids(target_field_name, objs)
    can_ignore_conflicts, must_send_signals, can_fast_add = manager._get_add_plan(
        db, source_field_name
    )

    def rows(ids: Any) -> list[Any]:
        return [
            through(
                **{
                    f"{source_field_name}_id": manager.related_val[0],
                    f"{target_field_name}_id": target_id,
                }
            )
            for target_id in ids
        ]

    if can_fast_add:
        await through.async_objects.using(db).abulk_create(
            rows(target_ids), ignore_conflicts=True
        )
        return

    present = {
        value
        async for value in through.async_objects.using(db)
        .filter(
            **{
                source_field_name: manager.related_val[0],
                f"{target_field_name}__in": target_ids,
            }
        )
        .values_list(target_field_name, flat=True)
    }
    missing_target_ids = target_ids.difference(present)
    async with async_atomic(using=db, savepoint=False):
        if must_send_signals:
            await _send(manager, "pre_add", missing_target_ids, db)
        # Add the ones that aren't there already.
        await through.async_objects.using(db).abulk_create(
            rows(missing_target_ids), ignore_conflicts=can_ignore_conflicts
        )
        if must_send_signals:
            await _send(manager, "post_add", missing_target_ids, db)


async def _aremove_items(manager: Any, db: str, old_ids: Any) -> None:
    # Django's ``ManyRelatedManager._remove_items`` for the ids ``set()``
    # removes, including the mirror rows of a symmetrical relation.
    if not old_ids:
        return
    async with async_atomic(using=db, savepoint=False):
        await _send(manager, "pre_remove", old_ids, db)
        filters = manager._build_remove_filters(old_ids)
        await manager.through.async_objects.using(db).filter(filters).adelete()
        await _send(manager, "post_remove", old_ids, db)


async def _send(manager: Any, action: str, pk_set: Any, db: str) -> None:
    await m2m_changed.asend(
        sender=manager.through,
        action=action,
        instance=manager.instance,
        reverse=manager.reverse,
        model=manager.model,
        pk_set=pk_set,
        using=db,
        raw=False,
    )
