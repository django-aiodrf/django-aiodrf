"""Protect branch selection, required-check behavior and artifact-only releases."""

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit
WORKFLOWS = Path(__file__).resolve().parents[1] / ".github/workflows"


def workflow(name):
    return yaml.safe_load((WORKFLOWS / name).read_text())


@pytest.mark.parametrize("branch", ["main", "dev"])
@pytest.mark.parametrize("outcome", ["success", "failure", "skipped", "cancelled"])
def test_gate_requires_success_for_every_required_branch_job(branch, outcome):
    jobs = workflow("tests.yml")["jobs"]
    gate = jobs["quality-gate"]
    step = gate["steps"][0]
    results = {name: {"result": "success"} for name in gate["needs"]}
    results["unit" if branch == "dev" else "ecosystem"]["result"] = outcome
    # Execute only the checked-in Python gate, not a shell or interpolated input.
    script = step["run"].split("\n", 1)[1].removesuffix("PY\n")
    process = subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, "RESULTS": json.dumps(results), "TARGET_BRANCH": branch},
        capture_output=True,
        text=True,
        check=False,
    )
    assert (process.returncode == 0) is (outcome == "success"), process.stderr


def test_dev_uses_only_unit_and_static_checks():
    jobs = workflow("tests.yml")["jobs"]
    assert "github.base_ref == 'dev'" in jobs["unit"]["if"]
    assert "unit-" in jobs["unit"]["steps"][-1]["run"]
    for name in (
        "matrix",
        "compatibility",
        "examples",
        "ecosystem",
        "postgres",
        "mongodb",
    ):
        assert "github.base_ref == 'main'" in jobs[name]["if"]
        assert "'dev'" not in jobs[name]["if"]


def test_release_reuses_the_verified_immutable_artifact_without_checkout_or_tests():
    jobs = workflow("release.yml")["jobs"]
    publish = jobs["publish"]
    assert publish["needs"] == "verify"
    assert "performance" not in jobs
    assert "performance-review" not in jobs
    assert publish["environment"]["name"] == "pypi"
    assert publish["permissions"] == {"actions": "read", "id-token": "write"}
    assert all("run" not in step for step in publish["steps"])
    download, upload = publish["steps"]
    assert download["uses"].startswith("actions/download-artifact@")
    assert download["with"]["artifact-ids"] == "${{ needs.verify.outputs.artifact-id }}"
    assert download["with"]["run-id"] == "${{ needs.verify.outputs.run-id }}"
    assert upload["uses"].startswith("pypa/gh-action-pypi-publish@")
    package = workflow("tests.yml")["jobs"]["package"]
    assert package["needs"] == "quality-gate"
    assert "github.event_name == 'push'" in package["if"]
    assert "github.ref == 'refs/heads/main'" in package["if"]


def test_external_actions_are_pinned_to_full_commit_shas():
    for path in WORKFLOWS.glob("*.yml"):
        for job in workflow(path.name)["jobs"].values():
            for step in job.get("steps", []):
                action = step.get("uses", "")
                if action and not action.startswith("./"):
                    assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", action), action


def test_release_evidence_is_archived_only_after_publication():
    jobs = workflow("release.yml")["jobs"]
    archive = jobs["archive"]
    assert archive["needs"] == ["verify", "publish"]
    assert archive["permissions"] == {"contents": "write", "actions": "read"}
    assert not any("checkout" in step.get("uses", "") for step in archive["steps"])
    assert (
        "release-bundle/release-metadata/manifest.json" in archive["steps"][-1]["run"]
    )
    assert "performance-evidence" not in archive["steps"][-1]["run"]


def test_workflow_version_matrix_matches_nox_and_documentation():
    source = ast.parse((WORKFLOWS.parents[1] / "noxfile.py").read_text())
    constants = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in source.body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in {"MATRIX", "PYTHONS"}
    }
    jobs = workflow("tests.yml")["jobs"]
    matrix = jobs["matrix"]["strategy"]["matrix"]
    assert matrix["python"] == constants["PYTHONS"]
    assert set(matrix["pair"]) == {
        f"django{django}-drf{drf}" for django, drf in constants["MATRIX"]
    }
    assert {"session": "tests_freethreaded", "python": "3.14t"} in jobs[
        "compatibility"
    ]["strategy"]["matrix"]["include"]
    assert "compatibility" in jobs["quality-gate"]["needs"]


def test_main_requires_the_optional_asgi_server_contract():
    jobs = workflow("tests.yml")["jobs"]
    assert {"session": "asgi_servers", "python": "3.14"} in jobs["compatibility"][
        "strategy"
    ]["matrix"]["include"]
    assert "compatibility" in jobs["quality-gate"]["needs"]


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        (["docs/guides/testing.md"], True),
        (["README.md", "examples/basics/README.md"], True),
        (["CHANGELOG.md"], False),
        (["docs/conf.py"], False),
        (["docs/api.md", "src/aiodrf/views.py"], False),
        ([], False),
    ],
)
def test_docs_only_classification_is_conservative(paths, expected):
    from tools.ci_scope import docs_only

    assert docs_only(paths) is expected


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        (["examples/typed-schemas/demo/views.py"], True),
        (["examples/README.md", "examples/compose.yaml"], True),
        # Documentation alone is docs-only, whose step runs the link and
        # catalogue tests the examples' READMEs belong to.
        (["examples/basics/README.md"], False),
        (["examples/check.py", "examples/Dockerfile"], True),
        (["examples/basic/tests/test_api.py", "docs/guide.md"], False),
        (["examples/basic/manage.py", "src/aiodrf/views.py"], False),
        (["examples/README.md", "CHANGELOG.md"], False),
        (["examples2/test.py"], False),
        (["examples/../src/aiodrf/views.py"], False),
        ([], False),
    ],
)
def test_examples_only_classification_is_conservative(paths, expected):
    from tools.ci_scope import examples_only

    assert examples_only(paths) is expected


@pytest.mark.parametrize("branch", ["main", "dev"])
@pytest.mark.parametrize("quality", ["success", "failure", "cancelled"])
def test_examples_only_gate_requires_quality_but_not_tests(branch, quality):
    jobs = workflow("tests.yml")["jobs"]
    gate = jobs["quality-gate"]
    results = {name: {"result": "skipped"} for name in gate["needs"]}
    results["changes"]["result"] = "success"
    results["quality"]["result"] = quality
    script = gate["steps"][0]["run"].split("\n", 1)[1].removesuffix("PY\n")
    process = subprocess.run(
        [sys.executable, "-c", script],
        env={
            **os.environ,
            "RESULTS": json.dumps(results),
            "TARGET_BRANCH": branch,
            "EXAMPLES_ONLY": "true",
            "DOCS_ONLY": "false",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert (process.returncode == 0) is (quality == "success"), process.stderr


def test_examples_only_changes_skip_every_runtime_job_and_artifact():
    jobs = workflow("tests.yml")["jobs"]
    for name in (
        "unit",
        "matrix",
        "compatibility",
        "examples",
        "ecosystem",
        "postgres",
        "mongodb",
        "coverage",
        "package",
    ):
        assert "outputs.examples-only != 'true'" in jobs[name]["if"], name
    for step in jobs["quality"]["steps"]:
        command = step.get("run", "")
        if "pytest" in command or "lint typecheck workflows" in command:
            assert "outputs.examples-only != 'true'" in step["if"]


@pytest.mark.parametrize(
    "name", sorted(path.name for path in WORKFLOWS.glob("*.yml")), ids=str
)
def test_every_checkout_drops_its_credentials(name):
    for job_name, job in workflow(name)["jobs"].items():
        for step in job.get("steps", []):
            if step.get("uses", "").startswith("actions/checkout@"):
                options = step.get("with", {})
                assert options.get("persist-credentials") is False, job_name
