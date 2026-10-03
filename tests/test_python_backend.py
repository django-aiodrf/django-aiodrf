"""
``SERIALIZER_BACKEND = "python"``: compiled output without msgspec or
pydantic. Its parity with DRF is tested with the other backends' (the
``BACKENDS`` of the compiler test modules); these are its own contracts.
"""

import datetime
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path
from unittest import mock

import pytest
from django.test import override_settings
from fastdrf import compiler
from rest_framework import serializers as drf_serializers

from aiodrf import aio
from aiodrf.response import _plain_data
from tests.testapp.models import Author, Book, Edition

PYTHON = {"SERIALIZER_BACKEND": "python", "SERIALIZER_BACKEND_FALLBACK": "error"}


class Plain(drf_serializers.ModelSerializer):
    class Meta:
        model = Book
        fields = ["id", "title", "pages"]


class Dated(drf_serializers.ModelSerializer):
    class Meta:
        model = Edition
        fields = ["id", "code", "published", "released", "active", "rating", "price"]


def test_values_of_another_type_are_converted_by_drf():
    # A value the project set: the msgspec backend refuses it and DRF
    # converts it; so does the python backend.
    book = Book(pk=1, title=5, pages=True)
    expected = Plain(book).data
    assert expected == {"id": 1, "title": "5", "pages": 1}
    with override_settings(FASTDRF={"SERIALIZER_BACKEND": "python"}, AIODRF={}):
        assert aio.try_data(Plain(book)) == expected


def test_the_output_is_built_in_types_for_the_inline_renderer():
    edition = Edition(
        pk=1,
        code=uuid.uuid4(),
        published=datetime.datetime(2024, 1, 2, 3, 4, 5, tzinfo=datetime.UTC),
        released=datetime.date(2024, 1, 2),
        active=True,
        rating=1.5,
        price="1.50",
        book_id=1,
    )
    expected = Dated(edition).data
    with override_settings(FASTDRF=PYTHON, AIODRF={}):
        data = aio.try_data(Dated(edition))
    assert data == expected
    assert _plain_data(data)


@pytest.mark.django_db
def test_input_is_validated_by_drf():
    author = Author.objects.create(name="Ada")
    with (
        override_settings(FASTDRF=PYTHON, AIODRF={}),
        mock.patch("fastdrf.inputs.recognize") as recognize,
    ):
        serializer = Plain(data={"title": "t", "pages": 3})
        assert aio.try_is_valid(serializer) is True
        assert serializer.validated_data == {"title": "t", "pages": 3}
        assert aio.try_data(Plain(Book(pk=1, title="t", pages=3, author=author)))
    recognize.assert_not_called()


def test_it_needs_neither_msgspec_nor_pydantic():
    code = """
        import sys
        sys.modules["msgspec"] = None
        sys.modules["pydantic"] = None
        import django
        from django.conf import settings
        settings.configure(
            INSTALLED_APPS=["django.contrib.contenttypes", "rest_framework", "aiodrf"],
            DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
            FASTDRF={"SERIALIZER_BACKEND": "python", "SERIALIZER_BACKEND_FALLBACK": "error"},
        )
        django.setup()
        from django.contrib.contenttypes.models import ContentType
        from rest_framework import serializers
        from aiodrf import aio
        from fastdrf import compiler

        class Types(serializers.ModelSerializer):
            class Meta:
                model = ContentType
                fields = ["id", "app_label", "model"]

        source = ContentType(pk=1, app_label="a", model="m")
        assert compiler.compiled_for(Types(source)) is not None
        assert aio.try_data(Types(source)) == {"id": 1, "app_label": "a", "model": "m"}
    """
    root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        env={"PYTHONPATH": f"{root / 'src'}:{root}", "PATH": ""},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_the_check_needs_no_package_for_it():
    from aiodrf.checks import check_settings

    with override_settings(FASTDRF={"SERIALIZER_BACKEND": "python"}, AIODRF={}):
        assert not [
            error for error in check_settings(None) if error.id == "aiodrf.E004"
        ]


def test_the_inspection_command_reports_its_output_and_drfs_input():
    from io import StringIO

    from django.core.management import call_command

    out = StringIO()
    call_command(
        "aiodrf_inspect_serializers",
        "--backend",
        "python",
        "--serializer",
        f"{__name__}.Plain",
        stdout=out,
    )
    lines = out.getvalue().splitlines()
    assert lines[1:3] == [
        "  output compiled",
        "  input  DRF: the python backend compiles output only",
    ]
    assert compiler.report(Plain(), backend="python") is None
