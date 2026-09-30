"""Verify release artifacts against successful main-branch CI provenance."""

import argparse
import hashlib
import json
import os
import re
import tomllib
from pathlib import Path
from urllib.request import Request, urlopen


def approved_run(run: dict, repository: str, sha: str) -> bool:
    """Accept only this repository's successful push workflow for the exact SHA."""
    return (
        run.get("event") == "push"
        and run.get("head_branch") == "main"
        and run.get("head_sha") == sha
        and run.get("status") == "completed"
        and run.get("conclusion") == "success"
        and run.get("path", "").split("@")[0] == ".github/workflows/tests.yml"
        and run.get("head_repository", {}).get("full_name") == repository
    )


def _api(repository: str, endpoint: str) -> dict:
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repository):
        raise ValueError("Invalid GitHub repository")
    request = Request(
        "https://api.github.com/repos/" + repository + endpoint,
        headers={
            "Authorization": "Bearer " + os.environ["GH_TOKEN"],
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urlopen(request, timeout=30) as response:  # noqa: S310 -- fixed HTTPS origin
        return json.load(response)


def find_run(repository: str, sha: str) -> tuple[int, int]:
    """Return a successful run and its immutable distribution artifact ID."""
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Expected a full commit SHA")
    result = _api(
        repository,
        "/actions/workflows/tests.yml/runs?branch=main&event=push"
        "&status=success&per_page=100&head_sha=" + sha,
    )
    for run in result["workflow_runs"]:
        if not approved_run(run, repository, sha):
            continue
        artifacts = _api(
            repository, f"/actions/runs/{int(run['id'])}/artifacts?per_page=100"
        )
        for artifact in artifacts["artifacts"]:
            if artifact["name"] == "release-" + sha and not artifact["expired"]:
                return int(run["id"]), int(artifact["id"])
    raise ValueError("No successful main CI artifact for this SHA; rerun main CI first")


def manifest(root: Path, sha: str) -> dict:
    """Record the built files, source identity and versioned release notes."""
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    changelog = (root / "CHANGELOG.md").read_text()
    heading = re.search(
        r"^## \[" + re.escape(version) + r"\] - \d{4}-\d{2}-\d{2}$",
        changelog,
        re.MULTILINE,
    )
    notes = changelog[heading.start() :].split("\n## ", 1)[0] if heading else ""
    files = sorted([*(root / "dist").glob("*.whl"), *(root / "dist").glob("*.tar.gz")])
    if len(files) != 2 or len(list((root / "dist").glob("*.whl"))) != 1:
        raise ValueError("Expected exactly one wheel and one source distribution")
    return {
        "sha": sha,
        "version": version,
        "notes": notes,
        "files": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in files
        },
    }


def verify(root: Path, record: dict, sha: str, tag: str) -> None:
    """Reject mismatched source, unpublished notes and altered distributions."""
    if record["sha"] != sha or tag != "v" + record["version"]:
        raise ValueError("Tag, source SHA and artifact version must agree")
    if not record["notes"]:
        raise ValueError(
            "Date the version's changelog entry before tagging the release"
        )
    actual = {path.name for path in (root / "dist").iterdir() if path.is_file()}
    if actual != set(record["files"]):
        raise ValueError("Distribution files differ from the verified manifest")
    for name, digest in record["files"].items():
        if Path(name).name != name or name.startswith("."):
            raise ValueError("Invalid distribution filename")
        if hashlib.sha256((root / "dist" / name).read_bytes()).hexdigest() != digest:
            raise ValueError("Distribution checksum mismatch")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["find-run", "manifest", "verify"])
    parser.add_argument("--sha", required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--tag", default=os.environ.get("GITHUB_REF_NAME", ""))
    args = parser.parse_args()
    if args.command == "find-run":
        run_id, artifact_id = find_run(args.repository, args.sha)
        print(f"run-id={run_id}\nartifact-id={artifact_id}")
    elif args.command == "manifest":
        target = args.root / "release-metadata" / "manifest.json"
        target.parent.mkdir(exist_ok=True)
        target.write_text(json.dumps(manifest(args.root, args.sha), indent=2) + "\n")
    else:
        record = json.loads(
            (args.root / "release-metadata" / "manifest.json").read_text()
        )
        verify(args.root, record, args.sha, args.tag)


if __name__ == "__main__":
    main()
