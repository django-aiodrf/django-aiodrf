"""Optional RESTQL field selection; the vendor owns parsing and query syntax."""

from django_restql.mixins import DynamicFieldsMixin
from rest_framework import serializers

from aiodrf.viewsets import ReadOnlyModelViewSet

from .models import Book


class BookSerializer(DynamicFieldsMixin, serializers.ModelSerializer):
    class Meta:
        model = Book
        fields = ["id", "title"]


class Books(ReadOnlyModelViewSet):
    queryset = Book.objects.order_by("pk")
    serializer_class = BookSerializer
