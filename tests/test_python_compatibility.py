"""Keep maintained source and examples off deprecated coroutine APIs."""

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[1]


def test_coroutine_detection_and_contextmanager_annotations():
    for directory in (ROOT / "src", ROOT / "examples", ROOT / "tests/typing"):
        for path in directory.rglob("*.py"):
            if any(part.startswith(".") for part in path.relative_to(ROOT).parts):
                continue
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module in (
                    "asyncio",
                    "asgiref.sync",
                ):
                    assert not any(
                        item.name == "iscoroutinefunction" for item in node.names
                    ), path
                if (
                    isinstance(node, ast.AsyncFunctionDef)
                    and any(
                        ast.unparse(decorator).endswith("asynccontextmanager")
                        for decorator in node.decorator_list
                    )
                    and node.returns is not None
                ):
                    assert "AsyncIterator" not in ast.unparse(node.returns), path
