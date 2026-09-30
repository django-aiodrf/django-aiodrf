"""
The output of a generic view's create and update, compiled per class.

After ``is_valid()`` a serializer's fields exist on the instance, so they
could have been edited: the compiler then looks the encoder up by the
instance's fields (``compiler.signature``). A serializer that a generic view
built, validated and saved with framework code alone (no ``get_serializer*``
or ``perform_*`` of the project's, a class without methods of its own) has
the fields of its class, and its class's encoder is used.
"""

from unittest import mock

import pytest
from django.test import override_settings
from rest_framework import serializers as drf_serializers

from aiodrf import viewsets
from aiodrf.contrib import compiler
from aiodrf.test import AsyncAPIRequestFactory
from tests.testapp.models import Author

factory = AsyncAPIRequestFactory()
BACKENDS = ["msgspec", "pydantic", "python"]


class AuthorOut(drf_serializers.ModelSerializer):
    class Meta:
        model = Author
        fields = ["id", "name"]


class Authors(viewsets.ModelViewSet):
    queryset = Author.objects.all()
    serializer_class = AuthorOut
    authentication_classes = []
    permission_classes = []


class OwnPerform(Authors):
    def perform_create(self, serializer):
        serializer.save()

    def perform_update(self, serializer):
        serializer.save()


class Edited(Authors):
    # A project that edits the fields after they were built: the output
    # follows the instance's fields.
    def perform_create(self, serializer):
        serializer.save()
        serializer.fields["name"] = drf_serializers.SerializerMethodField()
        serializer.fields["name"].bind("name", serializer)
        serializer.get_name = lambda author: author.name.upper()


async def _write(view_class, backend, method):
    signature = mock.Mock(wraps=compiler.signature)
    with (
        override_settings(AIODRF={"SERIALIZER_BACKEND": backend}),
        mock.patch.object(compiler, "signature", signature),
    ):
        if method == "create":
            view = view_class.as_view({"post": "create"})
            response = await view(factory.post("/", {"name": "Ada"}, format="json"))
        else:
            author = await Author.objects.acreate(name="Bo")
            view = view_class.as_view({"put": "update"})
            response = await view(
                factory.put("/", {"name": "Ada"}, format="json"), pk=author.pk
            )
    return response, signature.call_count


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("method", ["create", "update"])
async def test_a_generic_write_uses_the_class_encoder(
    backend, method, worker_connections
):
    response, signatures = await _write(Authors, backend, method)
    assert response.status_code in (200, 201)
    assert response.data["name"] == "Ada"
    assert signatures == 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("method", ["create", "update"])
async def test_a_perform_of_the_projects_keeps_the_instance_lookup(
    backend, method, worker_connections
):
    response, signatures = await _write(OwnPerform, backend, method)
    assert response.status_code in (200, 201)
    assert response.data["name"] == "Ada"
    assert signatures > 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("backend", BACKENDS)
async def test_fields_the_project_edited_are_respected(backend, worker_connections):
    response, _ = await _write(Edited, backend, "create")
    assert response.status_code == 201
    assert response.data["name"] == "ADA"
