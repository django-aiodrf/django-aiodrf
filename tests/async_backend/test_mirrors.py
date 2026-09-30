"""
The DRF, Django and django-filter code ``aiodrf.contrib.async_backend``
follows line by line, with its evaluations awaited. The digests are of
Django 6.1, DRF 3.18 and django-filter 25/26 (``nox -s native_db``). When
one changes, read the new source, bring the native body in line and add the
digest.
"""

import hashlib
import inspect

from django.core.paginator import Paginator
from django_filters import FilterSet
from rest_framework import generics, pagination, serializers

from tests.testapp.models import Book

ManyRelatedManager = Book.tags.related_manager_cls

MIRRORED = {
    # aiodrf.contrib.async_backend.pagination
    (pagination.PageNumberPagination, "paginate_queryset"): {"42a5e9011bf7"},
    (Paginator, "page"): {"6c5087bf548a"},
    (pagination.LimitOffsetPagination, "paginate_queryset"): {"994337175b10"},
    (pagination.CursorPagination, "paginate_queryset"): {"be7a5cadd71e"},
    # aiodrf.contrib.async_backend.serializers
    (serializers.ModelSerializer, "create"): {"f1c708e8750d"},
    (serializers.ModelSerializer, "update"): {"961aab5523dc"},
    (ManyRelatedManager, "set_base"): {"e4f28dbb34ae"},
    (ManyRelatedManager, "_add_items"): {"fe737638eda8"},
    (ManyRelatedManager, "_remove_items"): {"4ca5797ac991"},
    # aiodrf.contrib.async_backend.views: NativeViewMixin.aget_object
    (generics.GenericAPIView, "get_object"): {"b468a66ac12d"},
    # aiodrf.contrib.async_backend.filters: FilterSet.filter_queryset
    (FilterSet, "filter_queryset"): {"490d502efa07"},
}


def test_mirrored_methods_have_not_changed():
    for (owner, name), known in MIRRORED.items():
        source = inspect.getsource(getattr(owner, name))
        digest = hashlib.sha256(source.encode()).hexdigest()[:12]
        assert digest in known, f"{owner.__qualname__}.{name}() changed: {digest}"
