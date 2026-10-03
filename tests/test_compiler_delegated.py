"""
django-fastdrf's delegated fields (``FASTDRF["DELEGATE_FIELDS"]``) in
aiodrf's views: a ``SerializerMethodField`` runs in the compiled output, and
the compiled output, which then runs the project's code, is produced where
DRF's representation would be, not on the event loop.
"""

import pytest
from django.test import override_settings
from fastdrf import compiler

from aiodrf import serializers, viewsets
from aiodrf.test import AsyncAPIRequestFactory
from tests.testapp.models import Author, Book

factory = AsyncAPIRequestFactory()
BACKENDS = ["msgspec", "pydantic", "python"]


class BookOut(serializers.ModelSerializer):
    # Queries: on the event loop it would raise SynchronousOnlyOperation.
    siblings = serializers.SerializerMethodField()

    class Meta:
        model = Book
        fields = ["id", "title", "siblings", "pages"]

    def get_siblings(self, book):
        return Book.objects.filter(author_id=book.author_id).count()


class Books(viewsets.ReadOnlyModelViewSet):
    queryset = Book.objects.order_by("id")
    serializer_class = BookOut
    authentication_classes = []
    permission_classes = []


async def _list(backend, **fastdrf):
    with override_settings(
        FASTDRF={"SERIALIZER_BACKEND": backend, **fastdrf}, AIODRF={}
    ):
        response = await Books.as_view({"get": "list"})(factory.get("/"))
    return response.status_code, response.data


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("backend", BACKENDS)
async def test_a_method_field_runs_off_the_event_loop(backend, worker_connections):
    author = await Author.objects.acreate(name="Ada")
    for index in range(2):
        await Book.objects.acreate(title=f"B{index}", isbn=f"i{index}", author=author)

    expected = await _list("drf")
    assert expected[0] == 200
    with override_settings(
        FASTDRF={"SERIALIZER_BACKEND": backend, "DELEGATE_FIELDS": True}
    ):
        report = compiler.report_details(BookOut(), "strict", backend)
    assert report.delegated == ("siblings",)
    assert (
        await _list(backend, DELEGATE_FIELDS=True, SERIALIZER_BACKEND_FALLBACK="error")
        == expected
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("many", [False, True])
async def test_aio_data_runs_the_compiled_output_off_the_event_loop(
    backend, many, worker_connections
):
    from aiodrf import aio

    author = await Author.objects.acreate(name="Ada")
    book = await Book.objects.acreate(title="B", isbn="i", author=author)
    source = [book] if many else book
    expected = await aio.data(BookOut(source, many=many))
    with override_settings(
        FASTDRF={
            "SERIALIZER_BACKEND": backend,
            "DELEGATE_FIELDS": True,
            "SERIALIZER_BACKEND_FALLBACK": "error",
        },
        AIODRF={},
    ):
        assert await aio.data(BookOut(source, many=many)) == expected
