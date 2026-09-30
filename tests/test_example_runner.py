"""Example installs must not target the parent development environment."""

import os
from types import SimpleNamespace

import pytest

from examples import check

pytestmark = pytest.mark.unit


def test_example_commands_use_their_own_environment(tmp_path, monkeypatch):
    examples = tmp_path / "examples"
    project = examples / "example"
    project.mkdir(parents=True)
    (project / "pyproject.toml").write_text('[project]\nname = "example"\n')
    monkeypatch.setattr(check, "__file__", str(examples / "check.py"))
    monkeypatch.setattr("sys.argv", ["check.py", "--check-only"])
    monkeypatch.setenv("VIRTUAL_ENV", "/parent/environment")
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", "/parent/environment")
    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "parent.settings")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(check.subprocess, "run", run)
    check.main()
    install = next(
        command for command, _ in calls if command[:3] == ["uv", "pip", "install"]
    )
    executable = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    assert install[install.index("--python") + 1] == str(project / ".venv" / executable)
    for _, kwargs in calls:
        assert kwargs["cwd"] == project
        assert (
            not {"VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "DJANGO_SETTINGS_MODULE"}
            & kwargs["env"].keys()
        )
