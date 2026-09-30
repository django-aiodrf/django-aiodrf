"""The documentation's links resolve, and it names what the tests cover."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCUMENTS = sorted(
    path
    for path in [
        ROOT / "README.md",
        ROOT / "CHANGELOG.md",
        ROOT / "CONTRIBUTING.md",
        ROOT / "RELEASE.md",
        ROOT / "MAINTAINING.md",
        ROOT / "AI_POLICY.md",
        *(ROOT / "docs").rglob("*.md"),
        *(ROOT / "benchmarks").rglob("*.md"),
        *(ROOT / "examples").rglob("*.md"),
    ]
    # Not hidden directories: virtual environments and tool caches.
    if not any(part.startswith(".") for part in path.relative_to(ROOT).parts)
)
LINK = re.compile(r"\]\(([^)\s]+)\)")
REFERENCE_LINK = re.compile(r"^\[[^]]+\]:\s+(\S+)")
# Public reference-style links are portable and independent of ignored forks.
REFERENCE_DOCUMENTS = [
    ROOT / "docs/framework_design.md",
    *(ROOT / "docs/guides").glob("*.md"),
    *(ROOT / "docs/architecture").glob("*.md"),
]


def slug(heading):
    # GitHub's anchors: lower case, punctuation dropped, spaces to hyphens.
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def anchors(path):
    in_code = False
    found = set()
    for line in path.read_text().splitlines():
        if line.startswith("```"):
            in_code = not in_code
        elif not in_code and line.startswith("#"):
            found.add(slug(line.lstrip("#")))
    return found


def links(path, *, reference_style=False):
    in_code = False
    for line in path.read_text().splitlines():
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        pattern = REFERENCE_LINK if reference_style else LINK
        for target in pattern.findall(line):
            if not re.match(r"^[a-z]+:", target):
                yield target


def _assert_links_resolve(document, targets):
    broken = []
    for target in targets:
        path, _, anchor = target.partition("#")
        resolved = (document.parent / path).resolve() if path else document
        if not resolved.exists() or (
            anchor and resolved.suffix == ".md" and anchor not in anchors(resolved)
        ):
            broken.append(target)
    assert not broken


@pytest.mark.parametrize("document", DOCUMENTS, ids=lambda p: str(p.relative_to(ROOT)))
def test_relative_links_resolve(document):
    _assert_links_resolve(document, links(document))


@pytest.mark.parametrize(
    "document", REFERENCE_DOCUMENTS, ids=lambda p: str(p.relative_to(ROOT))
)
def test_reference_links_resolve(document):
    _assert_links_resolve(document, links(document, reference_style=True))


def test_public_documentation_does_not_require_local_archives_or_forks():
    documents = [
        ROOT / "README.md",
        *(ROOT / "docs").rglob("*.md"),
    ]
    for document in documents:
        for target in [*links(document), *links(document, reference_style=True)]:
            path = (document.parent / target.split("#", 1)[0]).resolve()
            assert not any(
                path.is_relative_to(ROOT / ignored)
                for ignored in (".local", "forks", "benchmarks/results")
            ), (document, target)


def test_documented_local_module_commands_exist():
    pattern = re.compile(r"\bpython -m ((?:aiodrf|tests|tools)\.[a-zA-Z0-9_.]+)")
    for document in DOCUMENTS:
        for module in pattern.findall(document.read_text()):
            base = ROOT / "src" if module.startswith("aiodrf.") else ROOT
            path = base.joinpath(*module.split("."))
            assert (
                path.with_suffix(".py").is_file() or (path / "__main__.py").is_file()
            ), (document, module)


def test_reference_reader_ignores_fenced_placeholders_and_external_urls(tmp_path):
    document = tmp_path / "guide.md"
    document.write_text(
        "[source]: code.py\n"
        "[web]: https://example.com/\n"
        "```markdown\n[package]: PACKAGE_URL\n```\n"
        "[section]: #configuration\n"
    )
    assert list(links(document, reference_style=True)) == ["code.py", "#configuration"]


def test_the_ecosystem_test_index_names_every_ecosystem_test_module():
    index = (ROOT / "tests" / "ecosystem" / "README.md").read_text()
    modules = sorted(p.name for p in (ROOT / "tests" / "ecosystem").glob("test_*.py"))
    assert [name for name in modules if f"ecosystem/{name}" not in index] == []


def test_settings_summary_lists_every_runtime_setting():
    from aiodrf.settings import DEFAULTS

    reference = (ROOT / "docs/reference/settings.md").read_text()
    table = reference.split("## Settings summary", 1)[1].split(
        "## Resource lifecycle", 1
    )[0]
    documented = re.findall(r"^\|\s*`([A-Z_]+)`\s*\|", table, flags=re.MULTILINE)
    assert set(documented) == set(DEFAULTS)


def test_distribution_name_and_example_sources_agree():
    import tomllib

    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert project["name"] == "django-aiodrf"
    for path in (ROOT / "examples").glob("*/pyproject.toml"):
        config = tomllib.loads(path.read_text())
        assert "django-aiodrf" in config["tool"]["uv"]["sources"], path
        assert any(
            name.split("[", 1)[0] == "django-aiodrf"
            for name in config["project"]["dependencies"]
        ), path


# -- What the documents repeat from the code ---------------------------------------


def _python_block_after(text, heading):
    start = text.index("```python", text.index(heading)) + len("```python")
    return text[start : text.index("```", start)]


def test_the_settings_reference_contains_the_runtime_defaults():
    import ast

    from aiodrf.settings import DEFAULTS

    block = _python_block_after(
        (ROOT / "docs/reference/settings.md").read_text(), "## Default configuration"
    )
    assert ast.literal_eval(block.split("=", 1)[1].strip()) == DEFAULTS


def _matrix_rows(text, heading):
    rows = set()
    section = text[text.index(heading) :]
    for line in section.splitlines()[1:]:
        if line.startswith("## "):
            break
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if line.startswith("|") and cells[0][:1].isdigit():
            rows |= {(cells[0], drf.strip()) for drf in cells[1].split(",")}
    return rows


def test_the_documented_version_matrix_is_the_tested_one():
    import ast

    # Read, not imported: the test environments do not install nox.
    module = ast.parse((ROOT / "noxfile.py").read_text())
    (matrix,) = (
        node.value
        for node in module.body
        if isinstance(node, ast.Assign)
        and getattr(node.targets[0], "id", None) == "MATRIX"
    )
    tested = set(ast.literal_eval(matrix))
    assert _matrix_rows((ROOT / "README.md").read_text(), "## Compatibility") == tested
    assert (
        _matrix_rows(
            (ROOT / "docs/guides/releasing.md").read_text(), "## Supported versions"
        )
        == tested
    )


def test_the_checks_reference_lists_every_check_id():
    emitted = set(
        re.findall(
            r'id="(aiodrf\.[EW]\d{3})"',
            (ROOT / "src/aiodrf/checks.py").read_text(encoding="utf-8"),
        )
    )
    reference = (ROOT / "docs/reference/checks.md").read_text(encoding="utf-8")
    documented = set(
        re.findall(r"^\| `(aiodrf\.[EW]\d{3})` \|", reference, re.MULTILINE)
    )
    assert documented == emitted


def test_install_instructions_name_the_distribution():
    # The package is ``django-aiodrf`` on PyPI; ``aiodrf[...]`` would install
    # another project, if any.
    paths = [
        *(ROOT / "src/aiodrf").rglob("*.py"),
        *(ROOT / "docs").rglob("*.md"),
        ROOT / "README.md",
    ]
    wrong = [
        f"{path.relative_to(ROOT)}:{number}"
        for path in paths
        for number, line in enumerate(path.read_text().splitlines(), 1)
        if re.search(r"(?<![\w-])aiodrf\[[a-z-]+\]", line)
    ]
    assert wrong == []


def test_the_readme_links_the_repository_absolutely():
    # PyPI renders the README without the repository: its links and images
    # are absolute, to files of this repository's main branch.
    import tomllib

    readme = (ROOT / "README.md").read_text()
    repository = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"][
        "urls"
    ]["Repository"]
    prefixes = (
        f"{repository}/blob/main/",
        f"{repository}/tree/main/",
        repository.replace("github.com", "raw.githubusercontent.com") + "/main/",
    )
    targets = [*LINK.findall(readme), *re.findall(r'(?:src|srcset)="([^"]+)"', readme)]
    assert not [t for t in targets if not t.startswith(("http", "#", "mailto:"))]
    local = [
        t.removeprefix(prefix).split("#")[0]
        for t in targets
        for prefix in prefixes
        if t.startswith(prefix)
    ]
    assert local
    assert [path for path in local if not (ROOT / path).exists()] == []
