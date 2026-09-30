"""Annotated context-manager factories declare their generator protocol."""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("directory", ["src/aiodrf", "examples"])
def test_contextmanager_annotations_use_generator_types(directory):
    invalid = []
    for path in (ROOT / directory).rglob("*.py"):
        if any(part.startswith(".") for part in path.relative_to(ROOT).parts):
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.returns is None:
                continue
            for decorator in node.decorator_list:
                name = ast.unparse(decorator).rsplit(".", 1)[-1]
                expected = {
                    "contextmanager": "Generator",
                    "asynccontextmanager": "AsyncGenerator",
                }.get(name)
                if expected is None:
                    continue
                annotation = node.returns
                if isinstance(annotation, ast.Subscript):
                    annotation = annotation.value
                if ast.unparse(annotation).rsplit(".", 1)[-1] != expected:
                    invalid.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert invalid == []
