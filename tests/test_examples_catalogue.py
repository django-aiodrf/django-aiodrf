"""Keep runnable examples and settings documentation aligned with the source."""

import re
import tomllib
from pathlib import Path

from aiodrf.settings import DEFAULTS

ROOT = Path(__file__).resolve().parent.parent


def test_each_independent_example_is_catalogued_and_has_tests():
    examples = ROOT / "examples"
    catalogue = (examples / "README.md").read_text()
    for project in examples.glob("*/pyproject.toml"):
        directory = project.parent
        config = tomllib.loads(project.read_text())
        assert f"({directory.name}/README.md)" in catalogue
        readme = (directory / "README.md").read_text()
        assert "--host" in readme
        assert "--port" in readme
        assert "uv run" in readme
        assert (directory / "manage.py").is_file()
        assert (directory / ".python-version").is_file()
        assert "uv pip install" in readme
        assert "--no-sync" in readme
        assert list((directory / "tests").glob("test_*.py"))
        assert config["tool"]["uv"]["sources"]["django-aiodrf"]["path"] == "../.."


def test_feature_matrix_evaluates_every_setting():
    matrix = (ROOT / "examples" / "FEATURES.md").read_text()
    for name in DEFAULTS:
        assert f"| `{name}` |" in matrix


def test_settings_reference_describes_every_setting():
    reference = (ROOT / "docs/reference/settings.md").read_text()
    for name in DEFAULTS:
        assert f"### {name}\n" in reference


def test_every_ecosystem_dependency_has_an_example_entry():
    dependencies = [
        line
        for group in ("ecosystem", "support")
        for line in (ROOT / f"requirements/{group}/requirements.txt")
        .read_text()
        .splitlines()
    ]
    inventory = (ROOT / "examples/ECOSYSTEM.md").read_text()
    for dependency in dependencies:
        if not dependency or dependency.startswith("#"):
            continue
        name = re.split(r"[\[<>=!~]", dependency, maxsplit=1)[0]
        assert f"`{name}`" in inventory
