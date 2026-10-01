"""Public documentation generation and lightweight CI classification contracts."""

import sys
from pathlib import Path

import pytest

from tools.ci_scope import docs_only

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "path",
    [
        "llms.txt",
        "assets/aiodrf-logo/svg/aiodrf-logo.svg",
        "assets/aiodrf-logo/ico/favicon.ico",
        "RELEASE.md",
    ],
)
def test_public_documentation_assets_use_lightweight_checks(path):
    assert docs_only([path])


@pytest.mark.parametrize(
    "path", ["CHANGELOG.md", "assets/tool.py", "tools/llms.py", "src/README.md"]
)
def test_release_inputs_and_executable_files_require_full_checks(path):
    assert not docs_only(["README.md", path])


def test_llms_index_is_deterministic_and_excludes_private_material(tmp_path):
    from tools.llms import render

    (tmp_path / "README.md").write_text("# Project\n\nIntroduction.\n")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "z.md").write_text("# Z\n\nLast guide.\n")
    (docs / "a.md").write_text("# A\n\nFirst guide.\n")
    (docs / ".secret.md").write_text("# Secret\n")
    (tmp_path / ".local").mkdir()
    (tmp_path / ".local/private.md").write_text("# Private\n")
    result = render(tmp_path)
    assert result == render(tmp_path)
    assert result.index("docs/a.md") < result.index("docs/z.md")
    assert "secret" not in result
    assert "Private" not in result


def test_llms_index_rejects_symlinks(tmp_path):
    from tools.llms import render

    (tmp_path / "README.md").write_text("# Project\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/link.md").symlink_to(tmp_path / "README.md")
    with pytest.raises(ValueError, match="symlink"):
        render(tmp_path)


def test_llms_includes_example_setup_guides_without_environment_files(tmp_path):
    from tools.llms import render

    examples = tmp_path / "examples"
    examples.mkdir()
    (examples / "CONTAINERS.md").write_text("# Containers\n")
    environment = examples / "basic/.venv"
    environment.mkdir(parents=True)
    (environment / "vendor.md").write_text("# Internal dependency\n")
    result = render(tmp_path)
    assert "examples/CONTAINERS.md" in result
    assert "vendor.md" not in result


def test_llms_index_matches_current_public_documentation():
    from tools.llms import render

    root = Path(__file__).resolve().parents[1]
    assert (root / "llms.txt").read_text() == render(root)
    assert not (root / "assets/llms.txt").exists()


def test_llms_links_resolve_relative_to_the_repository_root():
    import re

    from tools.llms import ROOT, render

    for target in re.findall(r"\]\(([^)]+)\)", render()):
        assert not target.startswith("../"), target
        assert (ROOT / target).is_file(), target


def test_documentation_wordmarks_resolve_to_supplied_assets():
    import re

    root = Path(__file__).resolve().parents[1]
    # The README's are absolute, for PyPI: files of the main branch.
    raw = "https://raw.githubusercontent.com/django-aiodrf/django-aiodrf/main/"
    for document in (root / "README.md", root / "docs/README.md"):
        targets = re.findall(r'(?:src|srcset)="([^"]+)"', document.read_text())
        assert len(targets) == 2
        for target in targets:
            path = target
            if document.name == "README.md" and document.parent == root:
                assert target.startswith(raw)
                path = str(root / target.removeprefix(raw))
            asset = (document.parent / path).resolve()
            assert asset.is_relative_to(root / "assets/aiodrf-logo")
            assert asset.is_file()


def test_the_site_shows_the_wordmark_for_its_own_colour_mode():
    # GitHub picks the wordmark by the reader's system colour scheme; the
    # site has its own light/dark mode, which the system's need not match.
    from tools.docs_site import theme_logos

    html = (
        "<p>Before</p>\n<picture>\n"
        '  <source media="(prefers-color-scheme: dark)" srcset="dark.svg">\n'
        '  <img src="light.svg" alt="django-aiodrf" width="420" height="93">\n'
        "</picture>\n<p>After</p>"
    )
    assert theme_logos(html) == (
        "<p>Before</p>\n"
        '<img class="theme-light-only" src="light.svg" alt="django-aiodrf" '
        'width="420" height="93">'
        '<img class="theme-dark-only" src="dark.svg" alt="django-aiodrf" '
        'width="420" height="93">'
        "\n<p>After</p>"
    )
    assert theme_logos("<p>No picture</p>") == "<p>No picture</p>"


def test_site_manifest_excludes_private_and_generated_files(tmp_path):
    from tools.docs_site import public_paths

    for name in (
        "docs/guide.md",
        "examples/basic/README.md",
        ".local/private.md",
        "forks/private.md",
        "docs/.hidden.md",
        "examples/basic/.venv/README.md",
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Example\n")
    names = {path.relative_to(tmp_path).as_posix() for path in public_paths(tmp_path)}
    assert {"docs/guide.md", "examples/basic/README.md"} <= names
    assert not any(
        "private" in name or ".hidden" in name or ".venv" in name for name in names
    )


def test_site_manifest_rejects_symlinks(tmp_path):
    from tools.docs_site import public_paths

    (tmp_path / "docs").mkdir()
    (tmp_path / "private.txt").write_text("private")
    (tmp_path / "docs/link.md").symlink_to(tmp_path / "private.txt")
    with pytest.raises(ValueError, match="symlink"):
        public_paths(tmp_path)


def test_site_build_is_checked_for_documentation_only_changes():
    import yaml

    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/tests.yml").read_text())
    steps = workflow["jobs"]["quality"]["steps"]
    (build,) = (step for step in steps if "nox -s docs" in step.get("run", ""))
    assert "if" not in build


def test_precommit_excludes_local_archives_and_uses_project_ruff():
    import re

    import yaml

    root = Path(__file__).resolve().parents[1]
    config = yaml.safe_load((root / ".pre-commit-config.yaml").read_text())
    for path in (".local/test.py", "forks/example.py", ".venv/module.py"):
        assert re.search(config["exclude"], path)
    (local,) = (repo for repo in config["repos"] if repo["repo"] == "local")
    entries = {hook["id"]: hook["entry"] for hook in local["hooks"]}
    assert entries["ruff-check"] == "ruff check"
    assert entries["ruff-format"] == "ruff format --check"


def test_the_site_check_finds_assets_a_page_references_but_the_build_lacks(tmp_path):
    from tools.docs_site import missing_assets

    (tmp_path / "guide").mkdir()
    (tmp_path / "css").mkdir()
    (tmp_path / "css/base.css").write_text("")
    (tmp_path / "index.html").write_text(
        '<link href="css/base.css"><script src="js/base.js"></script>'
        '<script src="https://cdn.example/x.js"></script>'
    )
    (tmp_path / "guide/index.html").write_text(
        '<link href="../css/base.css?v=1"><script src="../search/main.js"></script>'
    )
    (tmp_path / "404.html").write_text(
        '<link href="/docs/css/base.css"><script src="/docs/js/base.js"></script>'
        '<script src="/elsewhere/x.js"></script>'
    )
    assert missing_assets(tmp_path, "/docs/") == [
        "404.html: /docs/js/base.js",
        "404.html: /elsewhere/x.js",
        "guide/index.html: ../search/main.js",
        "index.html: js/base.js",
    ]


def test_the_built_site_keeps_the_themes_assets(tmp_path):
    # The hook replaces MkDocs' files with the public documentation; the
    # theme's CSS, JavaScript and the search script must stay in the site.
    pytest.importorskip("mkdocs")
    import subprocess

    root = Path(__file__).resolve().parents[1]
    subprocess.run(
        [
            sys.executable,
            "-m",
            "mkdocs",
            "build",
            "--quiet",
            "--site-dir",
            str(tmp_path),
        ],
        cwd=root,
        check=True,
    )
    from tools.docs_site import _site_base, missing_assets

    assert (tmp_path / "css/base.css").is_file()
    assert (tmp_path / "search/main.js").is_file()
    assert _site_base() == "/django-aiodrf/"
    assert missing_assets(tmp_path, _site_base()) == []
    index = (tmp_path / "index.html").read_text()
    assert "prefers-color-scheme" not in index
    assert 'class="theme-light-only"' in index
    assert 'class="theme-dark-only"' in index
    assert (tmp_path / "docs/stylesheets/aiodrf.css").is_file()
    assert "docs/stylesheets/aiodrf.css" in index
    # Readers can switch between the light and dark modes.
    assert "theme-toggle" in index or "data-bs-theme-value" in index
