"""Generate a deterministic llms.txt index of public repository documentation."""

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT_DOCUMENTS = (
    "README.md",
    "CONTRIBUTING.md",
    "AI_POLICY.md",
    "RELEASE.md",
    "MAINTAINING.md",
    "SECURITY.md",
    "CHANGELOG.md",
)


def _entry(root: Path, path: Path) -> str:
    relative = path.relative_to(root)
    if any(parent.is_symlink() for parent in (path, *path.parents) if parent != root):
        raise ValueError(f"Documentation must not be a symlink: {relative}")
    source = path.read_text(encoding="utf-8")
    heading = re.search(r"^# (.+)$", source, re.MULTILINE)
    if heading is None:
        raise ValueError(f"Missing document title: {relative}")
    title = heading[1].replace("[", r"\[").replace("]", r"\]")
    return f"- [{title}]({relative.as_posix()})"


def render(root: Path = ROOT) -> str:
    """Index explicit public roots without following links or fetching URLs."""
    sections = {
        "Project": [
            root / name for name in PROJECT_DOCUMENTS if (root / name).is_file()
        ],
        "Documentation": sorted((root / "docs").rglob("*.md")),
        "Examples": sorted((root / "examples").rglob("*.md")),
    }
    lines = [
        "# django-aiodrf",
        "",
        "> Awaitable Django REST framework views, serializers and policy hooks.",
        "",
        "Paths are relative to llms.txt at the repository root. Read the limitations",
        "and settings reference before selecting optional optimizations. Async views",
        "do not make synchronous Django ORM operations native asynchronous I/O.",
    ]
    for name, paths in sections.items():
        entries = [
            _entry(root, path)
            for path in paths
            if not any(part.startswith(".") for part in path.relative_to(root).parts)
        ]
        if entries:
            lines.extend(["", f"## {name}", "", *entries])
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Fail if llms.txt is stale"
    )
    args = parser.parse_args()
    target = ROOT / "llms.txt"
    content = render()
    if args.check:
        if not target.is_file() or target.read_text(encoding="utf-8") != content:
            raise SystemExit("Regenerate llms.txt with: python -m tools.llms")
    else:
        target.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
