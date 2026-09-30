"""Classify editorial and example-only changes without skipping unknown diffs."""

import json
import os
import re
import subprocess
from pathlib import Path, PurePosixPath


def examples_only(paths: list[str]) -> bool:
    # Documentation alone (an example's README) is docs_only: its tests check
    # the links and the catalogue the READMEs belong to.
    return (
        bool(paths)
        and all(
            path.startswith("examples/") and ".." not in PurePosixPath(path).parts
            for path in paths
        )
        and not docs_only(paths)
    )


def docs_only(paths: list[str]) -> bool:
    # Changelog and metadata participate in release identity, even if Markdown.
    return bool(paths) and all(
        path != "CHANGELOG.md"
        and (
            PurePosixPath(path).suffix == ".md"
            or path == "llms.txt"
            or (
                path.startswith("assets/")
                and PurePosixPath(path).suffix
                in {".png", ".jpg", ".webp", ".svg", ".ico"}
            )
        )
        and not path.startswith((".github/", "tools/", "src/", "tests/"))
        for path in paths
    )


def changed_paths(event: dict, name: str, head: str) -> list[str]:
    if name not in {"push", "pull_request"}:
        return []
    base = (
        event["pull_request"]["base"]["sha"]
        if name == "pull_request"
        else event.get("before", "")
    )
    if any(
        not re.fullmatch(r"[0-9a-f]{40}", value) or value == "0" * 40
        for value in (base, head)
    ):
        return []
    comparison = base + ("..." if name == "pull_request" else "..") + head
    result = subprocess.run(  # noqa: S603 -- validated commit IDs, fixed git options
        ["git", "diff", "--no-renames", "--name-only", "-z", comparison, "--"],  # noqa: S607 -- trusted runner PATH
        capture_output=True,
        check=False,
    )
    return (
        result.stdout.decode().rstrip("\0").split("\0")
        if result.returncode == 0 and result.stdout
        else []
    )


def main():
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    paths = changed_paths(
        event, os.environ["GITHUB_EVENT_NAME"], os.environ["GITHUB_SHA"]
    )
    print("docs-only=" + str(docs_only(paths)).lower())
    print("examples-only=" + str(examples_only(paths)).lower())


if __name__ == "__main__":
    main()
