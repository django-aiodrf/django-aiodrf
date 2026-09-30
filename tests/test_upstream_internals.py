"""
The names aiodrf reads that Django, DRF, asgiref and other packages do not
promise, one assertion each (docs/reference/upstream-internals.md).

A release that renames one fails here with the name, rather than somewhere
in a request with an ``AttributeError``, or silently where aiodrf reads it
with a default.
"""

import contextvars
import inspect
import weakref

import pytest
from asgiref.sync import SyncToAsync
from django.core.cache import caches
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse, StreamingHttpResponse
from django.template.response import SimpleTemplateResponse
from django.test import AsyncClient, AsyncRequestFactory
from django.utils.connection import ConnectionProxy
from django.utils.functional import SimpleLazyObject
from rest_framework import fields, serializers
from rest_framework import request as drf_request
from rest_framework.test import APIRequestFactory

from tests.testapp.models import Book


def source_mentions(owner, name):
    return name in inspect.getsource(owner)


class Plain(serializers.Serializer):
    name = serializers.CharField()


# -- Django ------------------------------------------------------------------------


def test_querysets_keep_their_result_cache():
    assert QuerySet(model=Book)._result_cache is None


def test_prefetching_fills_the_prefetched_objects_cache():
    from django.db.models import query

    assert source_mentions(query, "_prefetched_objects_cache")


def test_model_state_has_a_fields_cache():
    assert Book()._state.fields_cache == {}


def test_a_lazy_object_keeps_its_setup_function():
    def setup():
        return 1

    assert SimpleLazyObject(setup).__dict__["_setupfunc"] is setup


def test_a_connection_proxy_names_its_connections_and_alias():
    proxy = ConnectionProxy(caches, "default")
    assert proxy._connections is caches
    assert proxy._alias == "default"


def test_template_responses_record_rendering():
    response = SimpleTemplateResponse("unused.html")
    assert response._is_rendered is False
    assert response._post_render_callbacks == []


def test_responses_keep_their_reason_phrase_and_iterator():
    assert HttpResponse()._reason_phrase is None
    iterator = iter([b"a"])
    assert StreamingHttpResponse(iterator)._iterator is not None


def test_the_async_client_has_the_hooks_the_test_client_uses():
    assert callable(AsyncClient._ahandle_redirects)
    assert callable(AsyncRequestFactory._base_scope)
    from django.middleware import csrf

    assert source_mentions(csrf, "_dont_enforce_csrf_checks")


# -- DRF ---------------------------------------------------------------------------


def test_drf_requests_keep_the_django_request_and_their_data():
    request = drf_request.Request(HttpRequest())
    assert isinstance(request._request, HttpRequest)
    assert callable(request._load_data_and_files)
    assert callable(request._not_authenticated)
    assert source_mentions(drf_request.Request._load_data_and_files, "_full_data")
    assert callable(drf_request._hasattr)
    assert callable(drf_request.wrap_attributeerrors)


def test_fields_keep_their_constructor_arguments():
    assert fields.CharField(max_length=3)._kwargs == {"max_length": 3}


def test_serializers_keep_their_declarations_and_results():
    serializer = Plain(data={"name": "a"})
    assert list(Plain._declared_fields) == ["name"]
    assert [f.field_name for f in serializer._readable_fields] == ["name"]
    assert [f.field_name for f in serializer._writable_fields] == ["name"]
    assert serializer._read_only_defaults() == {}
    serializer.is_valid()
    assert serializer._validated_data == {"name": "a"}
    assert serializer._errors == {}


def test_the_request_factory_encodes_data():
    assert callable(APIRequestFactory()._encode_data)


def test_model_permissions_read_the_views_flag():
    from rest_framework import permissions

    assert source_mentions(
        permissions.DjangoModelPermissions, "_ignore_model_permissions"
    )


# -- asgiref -----------------------------------------------------------------------


def test_asgiref_maps_contexts_to_executors():
    assert isinstance(SyncToAsync.context_to_thread_executor, weakref.WeakKeyDictionary)
    assert isinstance(SyncToAsync.thread_sensitive_context, contextvars.ContextVar)


# -- Optional packages -------------------------------------------------------------


def test_drf_spectacular_isolates_view_methods_with_a_nested_function():
    drainage = pytest.importorskip("drf_spectacular.drainage")
    constants = drainage.isolate_view_method.__code__.co_consts
    assert any(inspect.iscode(constant) for constant in constants)


# django-mongodb-backend's ``features._supports_transactions``: in
# tests/ecosystem/mongodb/test_transactions.py, whose settings install it.
