import contextlib
import json
from io import StringIO
from types import ModuleType
from unittest.mock import patch

import msgspec
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from django.urls import path
from django.utils.module_loading import import_string
from rest_framework import serializers
from rest_framework import serializers as drf_serializers
from rest_framework.decorators import action

from aiodrf import generics
from aiodrf.contrib.msgspec.serializers import MsgspecSerializer
from aiodrf.contrib.typed import SchemaViewMixin
from aiodrf.routers import SimpleRouter
from aiodrf.viewsets import GenericViewSet
from tests.testapp.models import Author
from tests.testapp.serializers import AuthorSerializer


def inspect(*args):
    out = StringIO()
    call_command("aiodrf_inspect_serializers", *args, stdout=out)
    return out.getvalue().splitlines()


@pytest.mark.aiodrf_settings(SERIALIZER_BACKEND="msgspec")
def test_inspect_serializers():
    lines = inspect()
    author = lines.index("tests.testapp.serializers.AuthorSerializer")
    assert lines[author + 1 : author + 3] == ["  output compiled", "  input  compiled"]
    # The reason a serializer stays on DRF is part of the report.
    book = lines.index("tests.testapp.serializers.AsyncHookBookSerializer")
    assert (
        lines[book + 1]
        == "  output DRF: AsyncHookBookSerializer.summary has source='*'"
    )
    assert lines[book + 2].startswith("  input  DRF: AsyncHookBookSerializer")
    # One entry per serializer class, however many views use it.
    names = [line for line in lines if not line.startswith(" ")]
    assert len(names) == len(set(names))


def test_inspect_serializers_options():
    assert inspect("--parity", "fast") != []
    assert any(line == "  input  compiled" for line in inspect("--backend", "pydantic"))


def test_inspect_serializers_json_is_direction_and_backend_specific():
    records = json.loads(
        "\n".join(inspect("--backend", "pydantic", "--format", "json"))
    )
    author = next(
        record
        for record in records
        if record["serializer"].endswith(".AuthorSerializer")
    )
    assert author["backend"] == "pydantic"
    assert author["scope"] == "instance"
    assert author["directions"]["input"] == {
        "eligible": True,
        "code": "eligible",
        "reason": None,
        "delegated": [],
    }
    assert author["directions"]["output"] == {
        "eligible": True,
        "code": "eligible",
        "reason": None,
        "delegated": [],
    }


def test_explicit_serializer_paths_do_not_enumerate_views_and_are_deduplicated():
    with patch(
        "fastdrf.management.commands.fastdrf_inspect_serializers.EndpointEnumerator"
    ) as enumerator:
        records = json.loads(
            "\n".join(
                inspect(
                    "--serializer",
                    "tests.testapp.serializers.AuthorSerializer",
                    "--serializer",
                    "tests.testapp.serializers.AuthorSerializer",
                    "--format",
                    "json",
                )
            )
        )
    enumerator.assert_not_called()
    assert len(records) == 1
    assert records[0]["serializer"] == "tests.testapp.serializers.AuthorSerializer"
    assert records[0]["usages"] == []


@pytest.mark.parametrize(
    "path", ["no_such_module.Serializer", "builtins.str", "builtins.len"]
)
def test_explicit_invalid_serializer_is_a_command_error(path):
    with pytest.raises(CommandError, match="serializer"):
        inspect("--serializer", path)


def test_action_serializer_declarations_are_inspected_without_dynamic_view_execution():
    class DefaultSerializer(serializers.Serializer):
        name = serializers.CharField()

    class ActionSerializer(serializers.Serializer):
        value = serializers.IntegerField()

    class View(GenericViewSet):
        serializer_class = DefaultSerializer

        def get_serializer_class(self):
            raise AssertionError("no tenant/request code in static inspection")

        async def list(self, request):
            pass

        @action(detail=False, methods=["post"], serializer_class=ActionSerializer)
        async def submit(self, request):
            pass

    router = SimpleRouter()
    router.register("items", View, basename="items")
    urls = ModuleType("inspect_action_urls")
    urls.urlpatterns = router.urls
    with override_settings(ROOT_URLCONF=urls):
        records = json.loads("\n".join(inspect("--format", "json")))
    assert len(records) == 2
    action_record = next(
        record
        for record in records
        if record["serializer"].endswith(".ActionSerializer")
    )
    assert action_record["usages"] == [
        {
            "path": "/items/submit/",
            "method": "POST",
            "action": "submit",
            # The declaration is what was inspected; the view may choose another.
            "note": "get_serializer_class() may choose another serializer at request time",
        }
    ]


def test_a_serializer_chosen_by_the_async_hook_is_reported():
    class Declared(generics.ListAPIView):
        queryset = Author.objects.all()
        serializer_class = AuthorSerializer

        async def aget_serializer_class(self):
            return AuthorSerializer

    class Undeclared(Declared):
        serializer_class = None

    urls = ModuleType("inspect_async_hook_urls")
    urls.urlpatterns = [
        path("declared/", Declared.as_view()),
        path("undeclared/", Undeclared.as_view()),
    ]
    with override_settings(ROOT_URLCONF=urls):
        records = json.loads("\n".join(inspect("--format", "json")))
    by_path = {
        usage["path"]: (record, usage)
        for record in records
        for usage in record["usages"]
    }
    _, usage = by_path["/declared/"]
    assert usage["note"] == (
        "aget_serializer_class() may choose another serializer at request time"
    )
    record, _ = by_path["/undeclared/"]
    assert record["inspected"] is False
    assert "aget_serializer_class()" in record["reason"]


class Chosen(generics.ListAPIView):
    queryset = Author.objects.all()

    def get_serializer_class(self):
        return AuthorSerializer


class Undeclared(generics.ListAPIView):
    queryset = Author.objects.all()


class NeedsContext(drf_serializers.Serializer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.context["request"]


class NeedsContextView(generics.ListAPIView):
    queryset = Author.objects.all()
    serializer_class = NeedsContext


urlpatterns = [
    path("chosen/", Chosen.as_view()),
    path("undeclared/", Undeclared.as_view()),
    path("context/", NeedsContextView.as_view()),
    path(
        "authors/",
        generics.ListAPIView.as_view(
            queryset=Author.objects.all(), serializer_class=AuthorSerializer
        ),
    ),
]


@override_settings(ROOT_URLCONF=__name__)
def test_endpoints_that_were_not_inspected_are_listed():
    records = json.loads("\n".join(inspect("--format", "json")))
    for record in records:
        assert {"serializer", "inspected", "usages"} <= set(record), record
    by_path = {
        usage["path"]: record for record in records for usage in record["usages"]
    }
    assert by_path["/authors/"]["inspected"] is True
    assert by_path["/chosen/"]["inspected"] is False
    assert "get_serializer_class()" in by_path["/chosen/"]["reason"]
    assert by_path["/undeclared/"]["inspected"] is False
    assert by_path["/undeclared/"]["serializer"] is None
    assert by_path["/context/"]["inspected"] is False
    assert "could not be instantiated" in by_path["/context/"]["reason"]
    text = "\n".join(inspect())
    assert "/chosen/" in text
    assert "/undeclared/" in text


class ContextFields(drf_serializers.ModelSerializer):
    # DRF builds fields lazily: the constructor succeeds, the analysis does not.
    class Meta:
        model = Author
        fields = ["name"]

    def get_fields(self):
        self.context["request"]
        return super().get_fields()


class Interrupted(ContextFields):
    def get_fields(self):
        raise KeyboardInterrupt


def failing_for_authors(target):
    """``target`` (an analysis), raising for AuthorSerializer only."""
    original = import_string(target)

    def analysis(serializer, *args, **kwargs):
        if type(serializer) is AuthorSerializer:
            raise RuntimeError("analysis failed")
        return original(serializer, *args, **kwargs)

    return patch(target, analysis)


FAILING = {
    "constructor": (
        f"{__name__}.NeedsContext",
        None,
        "could not be instantiated: 'request'",
    ),
    "get_fields": (
        f"{__name__}.ContextFields",
        None,
        "could not be analyzed: KeyError: 'request'",
    ),
    "output": (
        "tests.testapp.serializers.AuthorSerializer",
        "fastdrf.management.commands.fastdrf_inspect_serializers.report_details",
        "could not be analyzed: RuntimeError: analysis failed",
    ),
    "input": (
        "tests.testapp.serializers.AuthorSerializer",
        "fastdrf.management.commands.fastdrf_inspect_serializers.report_input_details",
        "could not be analyzed: RuntimeError: analysis failed",
    ),
}


@pytest.mark.aiodrf_settings(SERIALIZER_BACKEND="msgspec")
@pytest.mark.parametrize("stage", FAILING)
def test_a_serializer_that_cannot_be_analyzed_is_reported_and_the_others_are(stage):
    failing, analysis, reason = FAILING[stage]
    passing = "tests.testapp.serializers.TagSerializer"
    args = ["--serializer", failing, "--serializer", passing]
    with failing_for_authors(analysis) if analysis else contextlib.nullcontext():
        records = json.loads("\n".join(inspect(*args, "--format", "json")))
        text = inspect(*args)
    assert records[0] == {
        "serializer": failing,
        "inspected": False,
        "usages": [],
        "reason": reason,
        "error": reason.rpartition(": ")[2],
    }
    assert records[1]["serializer"] == passing
    assert records[1]["inspected"] is True
    assert text[0] == f"{failing}: {reason}"
    assert text[1:3] == [passing, "  output compiled"]
    assert text[3].startswith("  input  ")
    assert len(text) == 4


def test_inspection_does_not_swallow_interruptions():
    with pytest.raises(KeyboardInterrupt):
        inspect("--serializer", f"{__name__}.Interrupted")


@pytest.mark.aiodrf_settings(SERIALIZER_BACKEND="msgspec")
def test_a_backend_that_is_not_installed_is_reported_per_direction():
    with patch(
        "fastdrf.management.commands.fastdrf_inspect_serializers.find_spec",
        return_value=None,
    ):
        records = json.loads(
            "\n".join(
                inspect(
                    "--format",
                    "json",
                    "--serializer",
                    "tests.testapp.serializers.AuthorSerializer",
                    "--serializer",
                    "tests.testapp.serializers.AsyncHookBookSerializer",
                )
            )
        )
    author, book = records
    missing = {
        "eligible": False,
        "code": "backend_not_installed",
        "reason": "msgspec is not installed",
        "delegated": [],
    }
    assert author["directions"] == {"output": missing, "input": missing}
    # What stays on DRF for another reason still says so.
    assert book["directions"]["output"]["code"] == "unsupported_source"
    assert book["directions"]["input"] == missing


class AuthorStruct(msgspec.Struct):
    name: str


class AuthorSchemaSerializer(MsgspecSerializer):
    class Meta:
        schema = AuthorStruct


def test_a_schema_serializer_is_reported_as_its_schemas():
    records = json.loads(
        "\n".join(
            inspect(
                "--format",
                "json",
                "--serializer",
                f"{__name__}.AuthorSchemaSerializer",
            )
        )
    )
    expected = {
        "eligible": False,
        "code": "schema_serializer",
        "reason": "msgspec validates and represents it",
        "delegated": [],
    }
    assert records[0]["directions"] == {"output": expected, "input": expected}
    lines = inspect("--serializer", f"{__name__}.AuthorSchemaSerializer")
    assert lines[1] == "  output schema: msgspec validates and represents it"


def test_schema_views_are_inspected_by_their_declarations():
    class Schemas(SchemaViewMixin, generics.ListCreateAPIView):
        queryset = Author.objects.all()
        input_schema = AuthorStruct

    class BareSchema(generics.ListAPIView):
        queryset = Author.objects.all()
        serializer_class = AuthorStruct

    urls = ModuleType("inspect_schema_urls")
    urls.urlpatterns = [
        path("schemas/", Schemas.as_view()),
        path("bare/", BareSchema.as_view()),
    ]
    with override_settings(ROOT_URLCONF=urls):
        records = json.loads("\n".join(inspect("--format", "json")))
    by_path = {
        usage["path"]: record for record in records for usage in record["usages"]
    }
    for endpoint in ("/schemas/", "/bare/"):
        record = by_path[endpoint]
        assert record["directions"]["output"]["code"] == "schema_serializer", record
        assert record["directions"]["input"]["code"] == "schema_serializer", record
