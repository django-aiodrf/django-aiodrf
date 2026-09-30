"""
Native async ORM for aiodrf views, with django-async-backend (opt-in).

Install ``django-aiodrf[async-backend]``, add ``"django_async_backend"`` to
``INSTALLED_APPS`` and use its PostgreSQL engine. Its app adds the async
members native writes need (``async_objects``, ``async_save``, ...) to every
model, by patching ``django.db.models.Model``; installing it is the
project's choice, aiodrf patches nothing. Then::

    from aiodrf.contrib.async_backend import ModelSerializer, ModelViewSet
    from aiodrf.contrib.async_backend.pagination import PageNumberPagination

    class BookSerializer(ModelSerializer):
        class Meta:
            model = Book
            fields = ["id", "title", "tags"]

    class BookViewSet(ModelViewSet):
        queryset = Book.async_objects.order_by("id")
        serializer_class = BookSerializer
        pagination_class = PageNumberPagination

Reads, writes and deletes run on the native connection in the request's
task. See ``docs/guides/async-backend.md``.
"""

from aiodrf.contrib.async_backend.pagination import (
    CursorPagination,
    LimitOffsetPagination,
    PageNumberPagination,
)
from aiodrf.contrib.async_backend.serializers import ModelSerializer, aset_many
from aiodrf.contrib.async_backend.views import (
    CreateAPIView,
    DestroyAPIView,
    GenericAPIView,
    GenericViewSet,
    ListAPIView,
    ListCreateAPIView,
    ModelViewSet,
    NativeViewMixin,
    ReadOnlyModelViewSet,
    RetrieveAPIView,
    RetrieveDestroyAPIView,
    RetrieveUpdateAPIView,
    RetrieveUpdateDestroyAPIView,
    UpdateAPIView,
)

__all__ = [
    "CreateAPIView",
    "CursorPagination",
    "DestroyAPIView",
    "GenericAPIView",
    "GenericViewSet",
    "LimitOffsetPagination",
    "ListAPIView",
    "ListCreateAPIView",
    "ModelSerializer",
    "ModelViewSet",
    "NativeViewMixin",
    "PageNumberPagination",
    "ReadOnlyModelViewSet",
    "RetrieveAPIView",
    "RetrieveDestroyAPIView",
    "RetrieveUpdateAPIView",
    "RetrieveUpdateDestroyAPIView",
    "UpdateAPIView",
    "aset_many",
]
