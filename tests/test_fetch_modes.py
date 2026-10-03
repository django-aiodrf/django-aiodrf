"""Django 6.1 fetch policies preserve the ordinary ORM execution boundary."""

import pytest
from django.core.exceptions import SynchronousOnlyOperation
from django.db import models
from django.test import TestCase, override_settings
from rest_framework import serializers

from aiodrf.compat import FETCH_PEERS, FETCH_RAISE, FieldFetchBlocked
from aiodrf.generics import ListAPIView
from tests.testapp.models import Author, Book

pytestmark = pytest.mark.skipif(FETCH_PEERS is None, reason="Django 6.1 fetch modes")


class BookOutput(serializers.ModelSerializer):
    author = serializers.StringRelatedField()

    class Meta:
        model = Book
        fields = ["title", "author"]


class Books(ListAPIView):
    serializer_class = BookOutput
    queryset = Book.objects.all()


class FetchPolicies(TestCase):
    @classmethod
    def setUpTestData(cls):
        for number in range(3):
            author = Author.objects.create(name=str(number))
            Book.objects.create(title=str(number), isbn=str(number), author=author)

    @override_settings(FASTDRF={"FETCH_MODE": "peers"}, AIODRF={})
    def test_generic_queryset_batches_missing_foreign_keys(self):
        view = Books()
        with self.assertNumQueries(2):
            rows = list(view.optimize_queryset(view.get_queryset()))
            assert [row.author.name for row in rows] == ["0", "1", "2"]

    def test_deferred_columns_use_peer_fetching(self):
        with self.assertNumQueries(2):
            rows = list(Book.objects.only("id").fetch_mode(models.FETCH_PEERS))
            assert [row.title for row in rows] == ["0", "1", "2"]

    def test_raise_accepts_loaded_relations_and_rejects_deferred_columns(self):
        with self.assertNumQueries(1):
            rows = list(Book.objects.select_related("author").fetch_mode(FETCH_RAISE))
            assert [row.author.name for row in rows] == ["0", "1", "2"]
        row = Book.objects.only("id").fetch_mode(FETCH_RAISE).first()
        with self.assertNumQueries(0), pytest.raises(FieldFetchBlocked):
            _ = row.title

    async def test_peers_does_not_make_lazy_access_async(self):
        rows = [row async for row in Book.objects.fetch_mode(FETCH_PEERS)]
        with pytest.raises(SynchronousOnlyOperation):
            _ = rows[0].author
