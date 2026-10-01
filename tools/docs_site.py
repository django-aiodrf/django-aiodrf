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
        ("docs/stylesheets", "*.css"),
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
    # The theme's and plugins' static files (CSS, JavaScript, fonts, the
    # search script) come from outside the documentation directory.
    docs_dir = Path(config.docs_dir).resolve()
    for file in files:
        documentation = file.abs_src_path is not None and (
            Path(file.abs_src_path).resolve().is_relative_to(docs_dir)
        )
        if not documentation and published.get_file_from_path(file.src_uri) is None:
            published.append(file)
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


# A README wordmark: GitHub chooses the light or dark image by the reader's
# system colour scheme.
_PICTURE = re.compile(
    r"<picture>\s*"
    r'<source media="\(prefers-color-scheme: dark\)" srcset="(?P<dark>[^"]+)">\s*'
    r'<img src="(?P<light>[^"]+)"(?P<attributes>[^>]*)>\s*'
    r"</picture>"
)


def theme_logos(html: str) -> str:
    """
    The README wordmarks as one image per site colour mode, which the site's
    stylesheet shows or hides: the site's mode is the reader's choice, and
    need not match the system's.
    """

    def images(match):
        attributes = match["attributes"]
        return (
            f'<img class="theme-light-only" src="{match["light"]}"{attributes}>'
            f'<img class="theme-dark-only" src="{match["dark"]}"{attributes}>'
        )

    return _PICTURE.sub(images, html)


def on_page_content(html, page, config, files):
    """MkDocs hook: wordmarks that follow the site's colour mode."""
    return theme_logos(html)


# A page's stylesheet or script: ``href``/``src`` ending in .css or .js.
_ASSET = re.compile(r'(?:href|src)="([^"#?]+\.(?:css|js))(?:[?#][^"]*)?"')


def missing_assets(site: Path, base: str = "/") -> list[str]:
    """
    The stylesheets and scripts built pages reference but the site lacks.
    ``base`` is the site's path on its host (``/django-aiodrf/``), which the
    root-relative references of the 404 page start with.
    """
    missing = []
    for page in sorted(site.rglob("*.html")):
        for target in _ASSET.findall(page.read_text(encoding="utf-8")):
            if re.match(r"[a-z][a-z0-9+.-]*:|//", target):
                continue  # another origin
            if target.startswith("/"):
                path = (
                    site / target.removeprefix(base)
                    if target.startswith(base)
                    else None
                )
            else:
                path = page.parent / target
            if path is None or not path.resolve().is_file():
                missing.append(f"{page.relative_to(site).as_posix()}: {target}")
    return missing


def _site_base() -> str:
    """The path of ``site_url`` in mkdocs.yml, ``/`` without one."""
    from urllib.parse import urlsplit

    config = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    url = re.search(r"^site_url:\s*(\S+)", config, re.MULTILINE)
    path = urlsplit(url[1]).path if url else "/"
    return path if path.endswith("/") else path + "/"


if __name__ == "__main__":
    import sys

    site = Path(sys.argv[1] if len(sys.argv) > 1 else "site")
    problems = missing_assets(site, _site_base())
    if problems:
        raise SystemExit("Missing site assets:\n" + "\n".join(problems))
