"""Keep the direct integration inputs and automated update scope aligned."""

import ast
import tomllib
from pathlib import Path

import pytest
import yaml
from packaging.requirements import Requirement

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[1]


def test_distribution_sessions_and_smoke_cover_every_declared_extra():
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())
    extras = set(metadata["project"]["optional-dependencies"])
    module = ast.parse((ROOT / "noxfile.py").read_text())
    (distribution,) = (
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "distribution"
    )
    (parameter,) = (
        decorator
        for decorator in distribution.decorator_list
        if isinstance(decorator, ast.Call)
        and getattr(decorator.func, "attr", None) == "parametrize"
    )
    assert set(ast.literal_eval(parameter.args[1])) == extras | {"core"}

    smoke = ast.parse((ROOT / "tests/distribution/smoke.py").read_text())
    (dependencies,) = (
        ast.literal_eval(node.value)
        for node in smoke.body
        if isinstance(node, ast.Assign)
        and getattr(node.targets[0], "id", None) == "dependencies"
    )
    assert set(dependencies) == extras


@pytest.mark.parametrize("group", ["ecosystem", "support", "servers"])
def test_integration_inputs_are_direct_unique_version_floors(group):
    path = ROOT / f"requirements/{group}/requirements.txt"
    requirements = [
        Requirement(line)
        for line in path.read_text().splitlines()
        if line and not line.startswith("#")
    ]
    assert len(requirements) == len(
        {requirement.name.lower() for requirement in requirements}
    )
    for requirement in requirements:
        assert requirement.url is None
        assert requirement.marker is None
        (specifier,) = requirement.specifier
        assert specifier.operator == ">="
        assert "*" not in specifier.version


def test_framework_and_support_inputs_are_disjoint():
    groups = {
        group: {
            Requirement(line).name.lower()
            for line in (ROOT / f"requirements/{group}/requirements.txt")
            .read_text()
            .splitlines()
            if line and not line.startswith("#")
        }
        for group in ("ecosystem", "support")
    }
    assert not groups["ecosystem"] & groups["support"]
    assert {"django-filter", "drf-spectacular", "channels"} <= groups["ecosystem"]
    assert {"msgspec", "pydantic", "httpx", "opentelemetry-api"} <= groups["support"]


@pytest.mark.parametrize("name", ["ecosystem", "ecosystem_elasticsearch"])
def test_ecosystem_sessions_resolve_both_inputs_together(name):
    module = ast.parse((ROOT / "noxfile.py").read_text())
    (session,) = (
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    (installation,) = (
        node
        for node in ast.walk(session)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "install"
    )
    for constant in ("ECOSYSTEM_REQUIREMENTS", "ECOSYSTEM_SUPPORT_REQUIREMENTS"):
        index = next(
            i
            for i, arg in enumerate(installation.args)
            if getattr(arg, "id", None) == constant
        )
        assert ast.literal_eval(installation.args[index - 1]) == "-r"


def test_dependabot_and_nox_use_the_same_integration_requirements():
    module = ast.parse((ROOT / "noxfile.py").read_text())
    (requirements,) = (
        ast.literal_eval(node.value)
        for node in module.body
        if isinstance(node, ast.Assign)
        and getattr(node.targets[0], "id", None) == "ECOSYSTEM_REQUIREMENTS"
    )
    updates = yaml.safe_load((ROOT / ".github/dependabot.yml").read_text())["updates"]
    (integration,) = (item for item in updates if item["package-ecosystem"] == "pip")
    assert Path(requirements).parent.as_posix() == integration["directory"].lstrip("/")
    assert integration["target-branch"] == "dev"
    assert integration["versioning-strategy"] == "increase"
    assert integration["schedule"]["interval"] == "weekly"
    assert not (ROOT / ".github/workflows/dependencies.yml").exists()
