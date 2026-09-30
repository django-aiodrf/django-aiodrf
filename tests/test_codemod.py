import importlib
import textwrap

import pytest

from aiodrf.codemod.transform import ADRF_METHODS, EXPORTS, transform_source


def rewrite(source):
    return transform_source(textwrap.dedent(source)).code


@pytest.mark.parametrize("module", sorted(EXPORTS))
def test_exports_table_matches_modules(module):
    aiodrf_module = importlib.import_module(f"aiodrf.{module}")
    drf_module = importlib.import_module(f"rest_framework.{module}")
    names = EXPORTS[module]
    if names is None:
        names = {n for n in vars(drf_module) if not n.startswith("_")}
    for name in names:
        if hasattr(drf_module, name):  # some names only exist in newer DRF versions
            assert hasattr(aiodrf_module, name), f"aiodrf.{module} lacks {name}"


def test_drf_names_are_split():
    code = rewrite(
        """
        from rest_framework.views import APIView, set_rollback
        from rest_framework.response import Response
        from rest_framework.status import HTTP_200_OK
        """
    )
    assert code == textwrap.dedent(
        """
        from rest_framework.views import set_rollback
        from aiodrf.views import APIView
        from aiodrf.response import Response
        from rest_framework.status import HTTP_200_OK
        """
    )


def test_drf_module_imports_follow_usage():
    code = rewrite(
        """
        from rest_framework import serializers, viewsets, pagination, status

        class S(serializers.ModelSerializer):
            pass

        class V(viewsets.ModelViewSet):
            pagination_class = pagination.PageNumberPagination
            other = pagination.BasePageNumberPagination
        """
    )
    assert "from rest_framework import pagination, status" in code
    assert "from aiodrf import serializers, viewsets" in code


def test_adrf_views_are_renamed():
    code = rewrite(
        """
        from adrf.viewsets import ModelViewSet
        from adrf.serializers import ModelSerializer
        from drf_spectacular.utils import extend_schema, extend_schema_view

        @extend_schema_view(alist=extend_schema(description="x"))
        class BookViewSet(ModelViewSet):
            async def alist(self, request, *args, **kwargs):
                return await super().alist(request, *args, **kwargs)

            async def perform_acreate(self, serializer):
                await serializer.asave(owner=self.request.user)

        class BookSerializer(ModelSerializer):
            async def acreate(self, validated_data):
                return await Book.objects.acreate(**validated_data)
        """
    )
    assert "from aiodrf.viewsets import ModelViewSet" in code
    assert "from aiodrf.serializers import ModelSerializer" in code
    assert "extend_schema_view(list=" in code
    assert "async def list(self, request" in code
    assert "await super().list(request" in code
    assert "async def aperform_create(self, serializer)" in code
    # Serializer hooks keep their names.
    assert "async def acreate(self, validated_data)" in code


def test_adrf_request_class():
    code = rewrite("from adrf.requests import AsyncRequest\n")
    assert code == "from aiodrf.request import Request as AsyncRequest\n"


def test_adrf_top_level_module_aliases_move_with_their_actions():
    source = "from adrf import viewsets as api, serializers as dto\nclass View(api.ModelViewSet):\n    async def alist(self, request):\n        return await super().alist(request)\n"
    result = transform_source(source)
    assert "from aiodrf import viewsets as api, serializers as dto" in result.code
    assert "await super().list(request)" in result.code
    assert transform_source(result.code).code == result.code


def test_cli(tmp_path, capsys):
    from aiodrf.codemod.__main__ import main

    path = tmp_path / "views.py"
    path.write_text("from rest_framework.views import APIView\n")
    assert main(["--check", str(tmp_path)]) == 1
    assert main([str(tmp_path)]) == 0
    assert path.read_text() == "from aiodrf.views import APIView\n"
    assert main(["--check", str(tmp_path)]) == 0


def test_nested_classes_are_classified_on_their_own():
    source = (
        "from adrf.viewsets import ModelViewSet\n"
        "class OuterView(ModelViewSet):\n"
        "    class SomeSerializer:\n"
        "        async def acreate(self, validated_data):\n"
        "            return await super().acreate(validated_data)\n"
        "    async def alist(self, request):\n"
        "        return await super().alist(request)\n"
    )
    code = transform_source(source).code
    # The serializer inside the view keeps its lifecycle methods ...
    assert "async def acreate(self, validated_data):" in code
    assert "await super().acreate(validated_data)" in code
    # ... and the view's own action is renamed together with its call.
    assert "async def list(self, request):" in code
    assert "await super().list(request)" in code
    # A second run changes nothing.
    assert transform_source(code).code == code


def test_aliased_view_bases_are_recognised():
    source = (
        "from adrf.viewsets import ModelViewSet as Base\n"
        "class Books(Base):\n"
        "    async def alist(self, request):\n"
        "        return await super().alist(request)\n"
    )
    assert "async def list(self, request):" in transform_source(source).code


def test_a_mixin_of_the_project_says_nothing_about_the_class():
    source = textwrap.dedent(
        """
        from adrf.serializers import ModelSerializer
        from adrf.viewsets import ModelViewSet

        class AuditMixin:
            pass

        class RecordSerializer(AuditMixin, ModelSerializer):
            async def acreate(self, validated_data):
                return await super().acreate(validated_data)

        class Records(AuditMixin, ModelViewSet):
            async def alist(self, request):
                return await super().alist(request)

            async def acreate(self, validated_data):
                return await super().acreate(validated_data)

        class Mystery(AuditMixin):
            async def alist(self, request):
                return await super().alist(request)
        """
    )
    result = transform_source(source)
    # The serializer keeps ``acreate`` and its ``super().acreate`` call.
    assert "return await super().acreate(validated_data)" in result.code
    assert "super().create(" not in result.code
    # The view's action is renamed with its call; its serializer-shaped
    # method is not an action and keeps both.
    assert (
        "async def list(self, request):\n        return await super().list(request)"
        in result.code
    )
    assert result.code.count("acreate") == 4
    # A class no base can classify is left alone, with a note.
    assert (
        "async def alist(self, request):\n        return await super().alist(request)"
        in result.code
    )
    assert result.notes == [
        (
            "`super().acreate()` in `class RecordSerializer`: aiodrf's serializers do "
            "not define acreate(); run DRF's `ModelSerializer.create(self, ...)` with "
            "`sync_to_async`"
        ),
        (
            "left `class Mystery` alone: it has adrf-style methods but its bases do not say "
            "whether it is a view"
        ),
    ]
    assert transform_source(result.code).code == result.code


ADRF_VIEW = "from adrf.views import APIView\n"


def test_a_directory_scan_leaves_environments_vendored_and_built_code_alone(tmp_path):
    from aiodrf.codemod.__main__ import main

    app = tmp_path / "app" / "views.py"
    skipped = [
        tmp_path / ".venv" / "lib" / "site-packages" / "adrf" / "views.py",
        tmp_path / ".nox" / "tests" / "views.py",
        tmp_path / "vendor" / "views.py",
        tmp_path / "build" / "lib" / "views.py",
        tmp_path / "dist" / "views.py",
        tmp_path / "node_modules" / "pkg" / "views.py",
        tmp_path / "app" / "migrations" / "0001_initial.py",
        tmp_path / "app" / "__pycache__" / "views.py",
    ]
    for path in (app, *skipped):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(ADRF_VIEW)
    assert main([str(tmp_path)]) == 0
    assert "aiodrf.views" in app.read_text()
    assert [path.read_text() for path in skipped] == [ADRF_VIEW] * len(skipped)
    # A file named explicitly is converted wherever it is.
    assert main([str(skipped[2])]) == 0
    assert "aiodrf.views" in skipped[2].read_text()


def test_a_rewrite_keeps_line_endings_encoding_and_mode(tmp_path):
    from aiodrf.codemod.__main__ import main

    crlf = tmp_path / "crlf.py"
    crlf.write_bytes(ADRF_VIEW.replace("\n", "\r\n").encode() + b"x = 1\r\n")
    crlf.chmod(0o640)
    latin = tmp_path / "latin.py"
    latin.write_bytes(
        b"# -*- coding: latin-1 -*-\n"
        + ADRF_VIEW.encode()
        + "name = 'caf\xe9'\n".encode("latin-1")
    )
    assert main([str(tmp_path)]) == 0
    assert crlf.read_bytes().count(b"\r\n") == 2
    assert b"\n" not in crlf.read_bytes().replace(b"\r\n", b"")
    assert crlf.stat().st_mode & 0o777 == 0o640
    rewritten = latin.read_bytes()
    assert b"aiodrf.views" in rewritten
    assert "name = 'caf\xe9'".encode("latin-1") in rewritten
    assert main(["--check", str(tmp_path)]) == 0


def test_codemod_table_covers_the_adrf_layers_names():
    from aiodrf.contrib.adrf_compat._views import _NAMES

    # The compatibility layer (and aiodrf.W006) and the codemod agree.
    assert _NAMES.items() <= ADRF_METHODS.items()


def test_as_view_action_maps_are_renamed():
    source = textwrap.dedent(
        """
        from adrf.viewsets import ModelViewSet

        books = BookViewSet.as_view({"get": "alist", "post": 'acreate'})
        book = BookViewSet.as_view(
            actions={"get": "aretrieve", "put": "aupdate", "patch": "partial_aupdate", "delete": "adestroy"},
            name="book",
        )
        """
    )
    result = transform_source(source)
    assert """as_view({"get": "list", "post": 'create'})""" in result.code
    assert (
        'actions={"get": "retrieve", "put": "update", "patch": "partial_update", '
        '"delete": "destroy"}'
    ) in result.code
    assert transform_source(result.code).code == result.code


def test_as_view_maps_keep_what_is_not_an_adrf_action_literal():
    source = textwrap.dedent(
        """
        name = "alist"
        a = Books.as_view({"get": "list", "post": "arecent"})
        b = Books.as_view({"get": name, "post": f"alist"})
        c = Books.as_view({"post": "perform_acreate"})
        d = something({"get": "alist"})
        e = Books.as_view(**{"get": "alist"})
        """
    )
    assert rewrite(source) == textwrap.dedent(source)


def test_cli_check_and_diff_see_as_view_maps(tmp_path, capsys):
    from aiodrf.codemod.__main__ import main

    path = tmp_path / "urls.py"
    source = 'view = Books.as_view({"get": "alist"})\n'
    path.write_text(source)
    assert main(["--check", str(tmp_path)]) == 1
    assert main(["--diff", str(tmp_path)]) == 0
    assert '+view = Books.as_view({"get": "list"})' in capsys.readouterr().out
    assert path.read_text() == source


# -- adrf imports: every name adrf exports still imports, or is noted --------------


def _adrf_names():
    import pkgutil
    import types
    import warnings

    with warnings.catch_warnings():
        # adrf itself uses ``asyncio.iscoroutinefunction``, deprecated in 3.14.
        warnings.simplefilter("ignore", DeprecationWarning)
        adrf = pytest.importorskip("adrf")
        modules = [
            importlib.import_module(f"adrf.{info.name}")
            for info in pkgutil.iter_modules(adrf.__path__)
        ]
    for module in modules:
        short = module.__name__.removeprefix("adrf.")
        for name, value in vars(module).items():
            if (
                not name.startswith("_")
                and not isinstance(value, types.ModuleType)
                and (getattr(value, "__module__", None) or "").startswith("adrf")
            ):
                yield short, name


def test_rewritten_adrf_imports_import_or_are_noted():
    failures = []
    for module, name in _adrf_names():
        result = transform_source(f"from adrf.{module} import {name}\n")
        if result.code.startswith("from adrf."):
            if not result.notes:
                failures.append(f"adrf.{module}.{name}: kept on adrf without a note")
            continue
        namespace = {}
        try:
            # The rewritten import itself, to prove it imports.
            exec(compile(result.code, "<codemod>", "exec"), namespace)  # noqa: S102
        except ImportError as exc:
            failures.append(f"adrf.{module}.{name}: {result.code!r}: {exc}")
            continue
        if name not in namespace:
            failures.append(f"adrf.{module}.{name}: local name lost: {result.code!r}")
    assert not failures, "\n".join(failures)


def test_adrf_permission_operators_become_drfs():
    code = rewrite(
        """
        from adrf.permissions import AAND, AsyncBasePermission
        from adrf.permissions import is_perm_operator
        """
    )
    assert (
        "from aiodrf.permissions import AND as AAND, BasePermission as AsyncBasePermission"
        in code
    )
    # adrf's helpers have no aiodrf counterpart: kept, with a note.
    assert "from adrf.permissions import is_perm_operator" in code


def test_kept_adrf_modules_are_noted():
    for source in (
        "from adrf.utils import getmembers\n",
        "from adrf import requests\n",
        "from adrf.mixins import get_data\n",
    ):
        result = transform_source(source)
        assert "adrf" in result.code, result.code
        assert result.notes, source


# -- adrf method references ----------------------------------------------------------


def test_references_follow_a_renamed_method_everywhere_in_the_class():
    code = rewrite(
        """
        from adrf.views import APIView

        class Books(APIView):
            async def acreate(self, request):
                return "created"

            async def post(self, request):
                return await self.acreate(request)
        """
    )
    assert "async def create(self, request)" in code
    assert "return await self.create(request)" in code


def test_adrf_hooks_are_renamed_in_every_method():
    code = rewrite(
        """
        from adrf.viewsets import ModelViewSet

        class Books(ModelViewSet):
            async def recent(self, request):
                page = self.paginate_queryset(self.get_queryset())
                return await self.get_apaginated_response(page)

            async def reload(self, request):
                return await self.alist(request)
        """
    )
    assert "await self.aget_paginated_response(page)" in code
    # Inherited from adrf's base: aiodrf's name.
    assert "await self.list(request)" in code


def test_a_reference_that_cannot_be_renamed_is_noted():
    result = transform_source(
        textwrap.dedent(
            """
            from project.views import BaseView

            class Books(BaseView):
                async def get(self, request):
                    return await self.alist(request)
            """
        )
    )
    assert "self.alist(request)" in result.code
    assert any("alist" in note for note in result.notes), result.notes


def test_an_action_with_adrfs_own_signature_is_renamed():
    # adrf declares ``ListModelMixin.alist(self, *args, **kwargs)``.
    code = rewrite(
        """
        from adrf.viewsets import ModelViewSet

        class Books(ModelViewSet):
            async def alist(self, *args, **kwargs):
                return await super().alist(*args, **kwargs)
        """
    )
    assert "async def list(self, *args, **kwargs)" in code
    assert "await super().list(*args, **kwargs)" in code


def test_super_acreate_in_a_serializer_is_noted():
    result = transform_source(
        textwrap.dedent(
            """
            from adrf.serializers import ModelSerializer

            class BookSerializer(ModelSerializer):
                async def acreate(self, validated_data):
                    return await super().acreate(validated_data)
            """
        )
    )
    assert "async def acreate(self, validated_data)" in result.code
    assert any("super().acreate" in note for note in result.notes), result.notes


# -- Formatting --------------------------------------------------------------------


def test_import_comments_and_trailing_commas_survive():
    source = (
        "from rest_framework.permissions import (\n"
        "    IsAuthenticated,  # auth\n"
        "    AllowAny,  # open\n"
        ")\n"
        "from rest_framework.views import (\n"
        "    APIView,  # the base of every view\n"
        "    set_rollback,\n"
        ")\n"
    )
    code = transform_source(source).code
    assert "    AllowAny,  # open\n)" in code
    assert "# the base of every view" in code
    assert transform_source(code).code == code


def test_a_directory_scan_does_not_enter_skipped_directories(tmp_path, monkeypatch):
    import os

    from aiodrf.codemod.__main__ import main

    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "views.py").write_text(ADRF_VIEW)
    deep = tmp_path / "node_modules" / "a" / "b"
    deep.mkdir(parents=True)
    (deep / "x.py").write_text(ADRF_VIEW)
    listed = []
    scandir = os.scandir

    def recording(path="."):
        listed.append(os.fspath(path))
        return scandir(path)

    monkeypatch.setattr(os, "scandir", recording)
    assert main([str(tmp_path)]) == 0
    assert not any("node_modules" in path for path in listed)


def test_a_symlinked_file_is_rewritten_through_the_link(tmp_path):
    from aiodrf.codemod.__main__ import main

    target = tmp_path / "real.py"
    target.write_text(ADRF_VIEW)
    link = tmp_path / "link.py"
    link.symlink_to(target)
    assert main([str(link)]) == 0
    assert link.is_symlink()
    assert "aiodrf.views" in target.read_text()


def test_a_diff_marks_a_missing_final_newline(tmp_path, capsys):
    from aiodrf.codemod.__main__ import main

    path = tmp_path / "views.py"
    path.write_text("from adrf.views import APIView")  # no final newline
    assert main(["--diff", str(path)]) == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert "-from adrf.views import APIView" in lines
    assert "+from aiodrf.views import APIView" in lines
    assert lines.count("\\ No newline at end of file") == 2
    assert out.endswith("\n")


@pytest.mark.parametrize(
    "source",
    [
        "import os; from adrf.views import APIView\n",
        "from adrf.views import APIView; import os\n",
        "if True: from adrf.views import APIView\n",
    ],
)
def test_imports_sharing_a_line_are_rewritten(source):
    code = transform_source(source).code
    assert "aiodrf.views import APIView" in code
    assert "adrf.views" not in code.replace("aiodrf.views", "")
    compile(code, "<rewritten>", "exec")


def test_a_module_alias_used_other_than_by_attribute_stays():
    source = textwrap.dedent(
        """
        from rest_framework import views as v

        other = v
        other.set_rollback()
        """
    )
    result = transform_source(source)
    assert "from rest_framework import views as v" in result.code
    assert any("views" in note for note in result.notes)


def test_a_function_nested_in_a_view_method_keeps_its_name():
    source = textwrap.dedent(
        """
        from adrf.views import APIView

        class Books(APIView):
            async def get(self, request):
                def alist(self, request):
                    return []

                return alist(self, request)
        """
    )
    code = transform_source(source).code
    assert "def alist(self, request):" in code
    assert "return alist(self, request)" in code


def test_an_attribute_named_like_a_module_alias_is_no_use_of_it():
    source = textwrap.dedent(
        """
        from rest_framework import views

        class Books(views.APIView):
            pass

        settings.views = 1
        """
    )
    assert "from aiodrf import views" in transform_source(source).code


def _project_with_a_broken_file(tmp_path):
    for name in ("a_views.py", "c_views.py"):
        (tmp_path / name).write_text("from rest_framework import views\n")
    (tmp_path / "b_broken.py").write_text("def broken(:\n")
    return tmp_path


def test_an_unparsable_file_does_not_stop_the_run(tmp_path, capsys):
    from aiodrf.codemod.__main__ import main

    project = _project_with_a_broken_file(tmp_path)
    assert main([str(project)]) == 2
    assert "aiodrf" in (project / "a_views.py").read_text()
    assert "aiodrf" in (project / "c_views.py").read_text()
    assert f"{project / 'b_broken.py'}: not parsed:" in capsys.readouterr().err


def test_check_tells_unparsable_files_from_changes(tmp_path, capsys):
    from aiodrf.codemod.__main__ import main

    project = _project_with_a_broken_file(tmp_path)
    assert main(["--check", str(project)]) == 2
    main([str(project)])
    capsys.readouterr()
    (project / "b_broken.py").write_text("x = 1\n")
    assert main(["--check", str(project)]) == 0


def test_without_libcst_the_command_says_what_to_install():
    import subprocess
    import sys
    import textwrap
    from pathlib import Path

    code = """
        import runpy
        import sys
        sys.modules["libcst"] = None
        sys.argv = ["aiodrf.codemod", "--help"]
        runpy.run_module("aiodrf.codemod", run_name="__main__")
    """
    root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        env={"PYTHONPATH": f"{root / 'src'}:{root}", "PATH": ""},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "pip install django-aiodrf[codemod]" in result.stderr
    assert "Traceback" not in result.stderr
