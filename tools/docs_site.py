"""Expose only reviewed public files to MkDocs, retaining repository link paths."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT_FILES = (
    "README.md",
    "CONTRIBUTING.md",
    "AI_POLICY.md",
    "RELEASE.md",
    "MAINTAINING.md",
    "SECURITY.md",
    "CODE_OF_CONDUCT.md",
    "CHANGELOG.md",
    "llms.txt",
    # Source attachments linked by the architecture and deployment guides.
    "src/aiodrf/utils.py",
    "src/aiodrf/settings.py",
    "src/aiodrf/aio/_common.py",
    "examples/Dockerfile",
    "examples/compose.yaml",
    "examples/container_entrypoint.py",
)


def public_paths(root: Path) -> list[Path]:
    """Never walk environments, local review archives, forks or build output."""
    paths = [root / name for name in PROJECT_FILES]
    for directory, pattern in (
        ("docs", "*.md"),
        ("examples", "*.md"),
        ("benchmarks", "*.md"),
        ("requirements", "*.txt"),
        ("assets/aiodrf-logo/svg", "*.svg"),
    ):
        paths.extend(
            path
            for path in (root / directory).rglob(pattern)
            if not any(part.startswith(".") for part in path.relative_to(root).parts)
        )
    for path in paths:
        if any(part.is_symlink() for part in (path, *path.parents) if part != root):
            raise ValueError(f"Public documentation cannot follow symlinks: {path}")
    return sorted(paths)


def on_files(files, config):
    """MkDocs hook: publish Markdown and explicit attachments, not the repo."""
    from mkdocs.structure.files import File, Files, InclusionLevel

    published = Files(
        File(
            path.relative_to(ROOT).as_posix(),
            src_dir=str(ROOT),
            dest_dir=config.site_dir,
            use_directory_urls=config.use_directory_urls,
            inclusion=InclusionLevel.INCLUDED,
        )
        for path in public_paths(ROOT)
    )
    # Keep the repository's index unchanged; site links need built page URLs.
    content = (ROOT / "llms.txt").read_text(encoding="utf-8")

    def site_link(match):
        target = published.get_file_from_path(match[1])
        if target is None:
            raise ValueError(f"Documentation index target is not published: {match[1]}")
        return f"]({target.url})"

    published.remove(published.get_file_from_path("llms.txt"))
    published.append(
        File.generated(
            config, "llms.txt", content=re.sub(r"\]\(([^)]+)\)", site_link, content)
        )
    )
    return published
