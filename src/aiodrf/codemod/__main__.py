"""Command-line entry point for the aiodrf migration tool."""

import argparse
import difflib
import io
import os
import pathlib
import shutil
import sys
import tempfile
import tokenize
from collections.abc import Iterable, Iterator, Sequence

from aiodrf.codemod import INSTALL

try:
    import libcst

    from aiodrf.codemod.transform import transform_source
except ImportError:
    sys.exit(INSTALL)

# Directories a scan does not enter: environments, vendored and built code,
# caches and migrations. Hidden directories (``.venv``, ``.nox``, ``.git``)
# are skipped too. A file named on the command line is always converted.
_SKIPPED_DIRECTORIES = frozenset(
    {
        "__pycache__",
        "build",
        "dist",
        "migrations",
        "node_modules",
        "site-packages",
        "vendor",
        "venv",
    }
)


def _files(paths: Iterable[str]) -> Iterator[pathlib.Path]:
    seen = set()
    for raw in paths:
        path = pathlib.Path(raw)
        found = sorted(_scan(path)) if path.is_dir() else [path]
        for file in found:
            key = file.resolve()
            if key not in seen:
                seen.add(key)
                yield file


def _scan(root: pathlib.Path) -> Iterator[pathlib.Path]:
    # Skipped directories are pruned, not entered and filtered afterwards.
    for directory, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            name
            for name in dirnames
            if name not in _SKIPPED_DIRECTORIES and not name.startswith(".")
        ]
        for name in filenames:
            path = pathlib.Path(directory, name)
            if name.endswith(".py") and not path.is_symlink():
                yield path


def _read(path: pathlib.Path) -> tuple[str, str]:
    # The file's own encoding (PEP 263) and line endings, as they are.
    raw = path.read_bytes()
    encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
    return raw.decode(encoding), encoding


def _write(path: pathlib.Path, text: str, encoding: str) -> None:
    # Replaced in one step, with the mode of the file it replaces. A link
    # named on the command line stays a link: its target is rewritten.
    path = path.resolve()
    handle, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(handle, "wb") as file:
            file.write(text.encode(encoding))
        shutil.copymode(path, temporary)
        os.replace(temporary, path)
    except BaseException:
        os.unlink(temporary)
        raise


def _diff(path: pathlib.Path, before: str, after: str) -> Iterator[str]:
    """A unified diff whose records all end, marking a missing final newline."""
    for line in difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=str(path),
        tofile=str(path),
    ):
        if line.endswith("\n"):
            yield line
        else:
            yield line + "\n\\ No newline at end of file\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m aiodrf.codemod", description=__doc__
    )
    parser.add_argument("paths", nargs="+")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--diff", action="store_true", help="print a diff instead of writing"
    )
    mode.add_argument(
        "--check", action="store_true", help="exit 1 if files would change"
    )
    args = parser.parse_args(argv)

    changed = unparsed = 0
    for path in _files(args.paths):
        try:
            source, encoding = _read(path)
            result = transform_source(source)
        except (libcst.ParserSyntaxError, SyntaxError, UnicodeDecodeError) as exc:
            # Templates, fixtures, Python 2 leftovers: the others are still
            # converted, and the exit status says that one was not.
            print(f"{path}: not parsed: {exc}", file=sys.stderr)
            unparsed += 1
            continue
        for note in result.notes:
            print(f"{path}: {note}", file=sys.stderr)
        if result.code == source:
            continue
        changed += 1
        if args.diff:
            sys.stdout.writelines(_diff(path, source, result.code))
        elif not args.check:
            _write(path, result.code, encoding)
            print(f"rewrote {path}")
    if unparsed:
        print(f"{unparsed} file(s) could not be parsed", file=sys.stderr)
        return 2
    if args.check and changed:
        print(f"{changed} file(s) would be rewritten", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
