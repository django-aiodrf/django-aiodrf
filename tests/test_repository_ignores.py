"""Generated state is ignored without hiding configuration and example sources."""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize(
    ("path", "ignored"),
    [
        (".coverage", True),
        (".coverage.worker.123", True),
        (".coveragerc", False),
        ("examples/basics/.venv/pyvenv.cfg", True),
        ("examples/basics/db.sqlite3-wal", True),
        ("examples/.env", True),
        ("examples/.env.example", False),
        ("examples/basics/.python-version", False),
        ("examples/compose.yaml", False),
        (".vscode/settings.json", True),
        (".idea/workspace.xml", True),
        ("AGENTS.md", True),
        ("forks/aiodrf-benchmarks/README.md", True),
    ],
)
def test_repository_ignore_boundaries(path, ignored):
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "--quiet", path],  # noqa: S607 -- repository tooling on PATH.
        cwd=ROOT,
        check=False,
        capture_output=True,
    )
    assert result.returncode in (0, 1), result.stderr.decode()
    assert (result.returncode == 0) is ignored
