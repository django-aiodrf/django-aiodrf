"""select_for_update() in aiodrf actions, on PostgreSQL (SQLite ignores the lock)."""

import psycopg
import pytest
from django.db import connections, transaction
from rest_framework import generics as drf_generics
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.test import APIRequestFactory

from aiodrf import generics
from aiodrf.test import AsyncAPIRequestFactory
from aiodrf.utils import run_sync
from tests.testapp.models import Author

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
async def close_worker_connections():
    yield
    await run_sync(connections.close_all)()


class AuthorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Author
        fields = ["name"]


class Locked:
    authentication_classes = []
    permission_classes = [AllowAny]
    serializer_class = AuthorSerializer
    queryset = Author.objects.select_for_update()


def locked_elsewhere(pk):
    """Whether another connection finds the row locked (NOWAIT)."""
    settings = connections["default"].settings_dict
    with psycopg.connect(
        host=settings["HOST"],
        port=settings["PORT"],
        user=settings["USER"],
        password=settings["PASSWORD"],
        dbname=settings["NAME"],
    ) as other:
        try:
            other.execute(
                "SELECT id FROM testapp_author WHERE id = %s FOR UPDATE NOWAIT", (pk,)
            )
        except psycopg.errors.LockNotAvailable:
            return True
        return False


async def patch(view, pk):
    request = AsyncAPIRequestFactory().patch("/", {"name": "Bo"}, format="json")
    return await view(request, pk=pk)


async def test_a_locking_queryset_outside_a_transaction_fails_as_in_drf():
    # get_object() evaluates the queryset before any transaction, in DRF too.
    author = await Author.objects.acreate(name="Ada")

    class Update(Locked, generics.UpdateAPIView):
        pass

    class DRFUpdate(Locked, drf_generics.UpdateAPIView):
        pass

    def drf_patch():
        request = APIRequestFactory().patch("/", {"name": "Bo"}, format="json")
        return DRFUpdate.as_view()(request, pk=author.pk)

    with pytest.raises(
        transaction.TransactionManagementError, match="select_for_update"
    ):
        await patch(Update.as_view(), author.pk)
    with pytest.raises(
        transaction.TransactionManagementError, match="select_for_update"
    ):
        await run_sync(drf_patch)()


async def test_locking_in_perform_update_holds_the_lock_for_the_save_only():
    author = await Author.objects.acreate(name="Ada")
    observed = []

    class Update(generics.UpdateAPIView):
        authentication_classes = []
        permission_classes = [AllowAny]
        serializer_class = AuthorSerializer
        queryset = Author.objects.all()

        def perform_update(self, serializer):
            # The idiomatic pattern: lock and write in one transaction.
            with transaction.atomic():
                Author.objects.select_for_update().get(pk=serializer.instance.pk)
                observed.append(locked_elsewhere(serializer.instance.pk))
                serializer.save()

    response = await patch(Update.as_view(), author.pk)
    assert response.status_code == 200
    assert observed == [True]
    assert not await run_sync(locked_elsewhere)(author.pk)
    assert (await Author.objects.aget(pk=author.pk)).name == "Bo"
