from __future__ import annotations

import ast
from pathlib import Path


def _find_time_sleep_calls(source: str) -> list[tuple[int, int]]:
    tree = ast.parse(source)
    hits: list[tuple[int, int]] = []

    class V(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
            try:
                fn = node.func
                if isinstance(fn, ast.Attribute) and fn.attr == 'sleep':
                    if isinstance(fn.value, ast.Name) and fn.value.id == 'time':
                        hits.append((int(getattr(node, 'lineno', 0)), int(getattr(node, 'col_offset', 0))))
            finally:
                self.generic_visit(node)

    V().visit(tree)
    return hits


def test_runtime_has_no_time_sleep_calls() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    runtime_dir = repo_root / 'runtime'
    assert runtime_dir.exists(), 'runtime/ directory missing'

    offenders: list[str] = []
    for path in runtime_dir.rglob('*.py'):
        src = path.read_text(encoding='utf-8', errors='replace')
        hits = _find_time_sleep_calls(src)
        if hits:
            offenders.append(f"{path.as_posix()}:{hits[0][0]}")

    assert not offenders, 'Found time.sleep() usage in runtime/: ' + ', '.join(offenders)
