"""Release reuse requires matching commit, workflow, branch and artifact bytes."""

import copy
import hashlib
import tomllib
from pathlib import Path

import pytest

from aiodrf import __version__
from tools import release
from tools.release import approved_run, find_run, manifest, verify

pytestmark = pytest.mark.unit
SHA = "a" * 40
RUN = {
    "event": "push",
    "head_branch": "main",
    "head_sha": SHA,
    "status": "completed",
    "conclusion": "success",
    "path": ".github/workflows/tests.yml",
    "head_repository": {"full_name": "owner/repo"},
}


def test_package_and_runtime_versions_match():
    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    assert project["version"] == __version__


def test_successful_main_commit_is_accepted():
    assert approved_run(RUN, "owner/repo", SHA)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("event", "pull_request"),
        ("head_branch", "dev"),
        ("head_sha", "b" * 40),
        ("status", "in_progress"),
        ("conclusion", "failure"),
        ("path", ".github/workflows/weekly.yml"),
        ("head_repository", {"full_name": "attacker/repo"}),
    ],
)
def test_other_runs_cannot_authorize_a_release(key, value):
    assert not approved_run({**RUN, key: value}, "owner/repo", SHA)


def test_artifact_version_notes_and_bytes_are_verified(tmp_path):
    (tmp_path / "dist").mkdir()
    path = tmp_path / "dist" / "django_aiodrf-0.0.1.tar.gz"
    path.write_bytes(b"test distribution")
    record = {
        "sha": SHA,
        "version": "0.0.1",
        "notes": "## [0.0.1] - 2026-09-26",
        "files": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()},
    }
    verify(tmp_path, record, SHA, "v0.0.1")
    for key, value in (("sha", "b" * 40), ("version", "0.2.0"), ("notes", "")):
        wrong = copy.deepcopy(record)
        wrong[key] = value
        with pytest.raises(ValueError, match=r"must agree|changelog"):
            verify(tmp_path, wrong, SHA, "v0.0.1")
    path.write_bytes(b"altered")
    with pytest.raises(ValueError, match="checksum"):
        verify(tmp_path, record, SHA, "v0.0.1")


@pytest.mark.parametrize("expired", [False, True])
def test_ci_lookup_selects_an_immutable_artifact_id(monkeypatch, expired):
    calls = []

    def api(repository, endpoint):
        calls.append((repository, endpoint))
        if "/runs?" in endpoint:
            return {
                "workflow_runs": [
                    {**RUN, "id": 11, "event": "pull_request"},
                    {**RUN, "id": 12},
                ]
            }
        assert endpoint == "/actions/runs/12/artifacts?per_page=100"
        return {"artifacts": [{"name": "release-" + SHA, "expired": expired, "id": 42}]}

    monkeypatch.setattr(release, "_api", api)
    if expired:
        with pytest.raises(ValueError, match="No successful main CI artifact"):
            find_run("owner/repo", SHA)
    else:
        assert find_run("owner/repo", SHA) == (12, 42)
    assert len(calls) == 2
    assert "head_sha=" + SHA in calls[0][1]


def test_ci_lookup_rejects_untrusted_sha_before_network(monkeypatch):
    def unexpected(*args):
        pytest.fail("No API request should have been made")

    monkeypatch.setattr(release, "_api", unexpected)
    with pytest.raises(ValueError, match="full commit SHA"):
        find_run("owner/repo", "main&event=pull_request")


def test_manifest_requires_both_distributions_and_versioned_notes(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.0.1"\n')
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## [Unreleased]\n")
    (tmp_path / "dist").mkdir()
    with pytest.raises(ValueError, match="one wheel and one source"):
        manifest(tmp_path, SHA)
    (tmp_path / "dist" / "django_aiodrf-0.0.1.tar.gz").write_bytes(b"sdist")
    (tmp_path / "dist" / "django_aiodrf-0.0.1-py3-none-any.whl").write_bytes(b"wheel")
    record = manifest(tmp_path, SHA)
    assert record["notes"] == ""
    with pytest.raises(ValueError, match="changelog"):
        verify(tmp_path, record, SHA, "v0.0.1")
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [0.0.1] - 2026-09-26\n\nInitial release.\n\n## [Older]\n"
    )
    record = manifest(tmp_path, SHA)
    assert "Initial release." in record["notes"]
    assert "[Older]" not in record["notes"]
    verify(tmp_path, record, SHA, "v0.0.1")
    (tmp_path / "dist" / "extra.whl").write_bytes(b"unverified")
    with pytest.raises(ValueError, match="files differ"):
        verify(tmp_path, record, SHA, "v0.0.1")
