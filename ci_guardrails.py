from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence, cast


@dataclass(frozen=True, slots=True)
class Violation:
    rule: str
    file: str
    detail: str


def _repo_root() -> Path:
    return Path(__file__).resolve().parent


def _is_python_file(path: Path) -> bool:
    return path.is_file() and path.suffix == '.py'


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(_repo_root().resolve()).as_posix()
    except Exception:
        return path.as_posix()


def _parse(path: Path) -> ast.Module:
    src = path.read_text(encoding='utf-8')
    return cast(ast.Module, ast.parse(src, filename=_rel(path)))


def _iter_calls(node: ast.AST) -> Iterable[ast.Call]:
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            yield n


def check_entrypoints_preflight_before_logger(*, root: Path | None = None) -> list[Violation]:
    """Entry point invariant: preflight must run before configure_logger().

    Heuristic: inside run_* functions in *_entrypoint.py, the first call to
    configure_logger() must be preceded by at least one call whose callee name
    contains 'preflight'.
    """

    root = root or _repo_root()
    violations: list[Violation] = []

    for path in sorted(root.glob('*_entrypoint.py')):
        if not _is_python_file(path):
            continue

        tree = _parse(path)

        run_fns = [
            n
            for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name.startswith('run_')
        ]
        if not run_fns:
            continue

        for fn in run_fns:
            logger_call_lineno: int | None = None
            preflight_before_logger = False

            for call in _iter_calls(fn):
                callee_name = None
                if isinstance(call.func, ast.Name):
                    callee_name = call.func.id
                elif isinstance(call.func, ast.Attribute):
                    callee_name = call.func.attr

                if callee_name == 'configure_logger':
                    if logger_call_lineno is None:
                        logger_call_lineno = int(getattr(call, 'lineno', 0) or 0)
                    continue

                if callee_name and 'preflight' in callee_name.lower():
                    lineno = int(getattr(call, 'lineno', 0) or 0)
                    if logger_call_lineno is None or lineno < logger_call_lineno:
                        preflight_before_logger = True

            if logger_call_lineno is None:
                violations.append(
                    Violation(
                        rule='preflight_before_runtime_logger',
                        file=_rel(path),
                        detail=f"{fn.name}() has no configure_logger() call",
                    )
                )
                continue

            if not preflight_before_logger:
                violations.append(
                    Violation(
                        rule='preflight_before_runtime_logger',
                        file=_rel(path),
                        detail=f"{fn.name}() calls configure_logger() without earlier preflight call",
                    )
                )

    return violations


def check_sleep_only_tick_pacing(*, root: Path | None = None) -> list[Violation]:
    """Disallow time.sleep in runtime and entrypoints.

    Scope: entrypoints and runtime code only.
    (Adapters may legitimately sleep for hardware IO; tests/tools may sleep as well.)
    """

    root = root or _repo_root()
    violations: list[Violation] = []

    candidates: list[Path] = []
    candidates.extend(sorted(root.glob('*_entrypoint.py')))
    runtime_dir = root / 'runtime'
    if runtime_dir.exists():
        candidates.extend(sorted(runtime_dir.rglob('*.py')))

    for path in candidates:
        if not _is_python_file(path):
            continue

        tree = _parse(path)

        for call in _iter_calls(tree):
            # time.sleep(...)
            is_time_sleep = (
                isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == 'time'
                and call.func.attr == 'sleep'
            )
            # sleep(...) (from time import sleep)
            is_sleep = isinstance(call.func, ast.Name) and call.func.id == 'sleep'

            if not (is_time_sleep or is_sleep):
                continue

            lineno = int(getattr(call, 'lineno', 0) or 0)
            violations.append(
                Violation(
                    rule='sleep_only_tick_pacing',
                    file=_rel(path),
                    detail=f"time.sleep usage not allowed (line {lineno})",
                )
            )

    return violations


def check_execute_one_input(*, root: Path | None = None) -> list[Violation]:
    """Runtime invariant: each execute_* function emits at most one input.* call.

    We conservatively count ANY call on the parameter named 'input_'.
    """

    root = root or _repo_root()
    violations: list[Violation] = []

    runtime_dir = root / 'runtime'
    if not runtime_dir.exists():
        return violations

    def _is_input_call(call: ast.Call) -> bool:
        return (
            isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == 'input_'
        )

    def _count_input_calls_in_node(node: ast.AST) -> int:
        return sum(1 for c in _iter_calls(node) if _is_input_call(c))

    def _max_input_calls_any_path(stmts: list[ast.stmt]) -> int:
        """Conservative upper-bound of input calls along any *single* execution path.

        - Handles If and Try as branching constructs.
        - Treats loops containing any input calls as potentially multi-input.
        """

        max_seen = 0

        def walk_stmt(stmt: ast.stmt) -> tuple[set[int], int]:
            """Return (possible_counts_after_stmt, local_max_seen)."""

            local_max = 0

            if isinstance(stmt, (ast.Return, ast.Raise)):
                # Exits immediately.
                return set(), 0

            if isinstance(stmt, ast.Expr):
                k = _count_input_calls_in_node(stmt)
                return {k}, k

            if isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Assert)):
                k = _count_input_calls_in_node(stmt)
                return {k}, k

            if isinstance(stmt, ast.If):
                then_counts, then_max = walk_block(stmt.body)
                else_counts, else_max = walk_block(stmt.orelse)
                return (then_counts | else_counts), max(then_max, else_max)

            if isinstance(stmt, (ast.For, ast.While)):
                # If the loop body can emit input at all, conservatively treat as multi-input capable.
                if _count_input_calls_in_node(stmt) > 0:
                    return {2}, 2
                # Otherwise, treat as 0.
                return {0}, 0

            if isinstance(stmt, ast.Try):
                body_counts, body_max = walk_block(stmt.body)
                orelse_counts, orelse_max = walk_block(stmt.orelse)
                final_counts, final_max = walk_block(stmt.finalbody)

                handler_counts: set[int] = set()
                handler_max = 0
                for h in stmt.handlers:
                    hc, hm = walk_block(h.body)
                    handler_counts |= hc
                    handler_max = max(handler_max, hm)

                # Any of: body (normal), body->orelse (normal), or handler paths.
                combined_counts = set()
                combined_counts |= body_counts
                combined_counts |= orelse_counts
                combined_counts |= handler_counts

                # Apply finally additions to all continuing paths.
                if final_counts:
                    combined_counts = {c + f for c in combined_counts for f in final_counts}

                return combined_counts, max(body_max, orelse_max, final_max, handler_max)

            # Fallback: count input calls inside the stmt as a single-step addition.
            k = _count_input_calls_in_node(stmt)
            return {k}, k

        def walk_block(block: list[ast.stmt]) -> tuple[set[int], int]:
            # Set of possible cumulative counts for paths that continue.
            counts: set[int] = {0}
            block_max = 0

            for stmt in block:
                next_counts: set[int] = set()
                stmt_counts, stmt_max = walk_stmt(stmt)
                block_max = max(block_max, stmt_max)

                if not stmt_counts:
                    # Statement is a guaranteed exit; stop.
                    counts = set()
                    break

                for c in counts:
                    for add in stmt_counts:
                        next_counts.add(c + add)
                counts = next_counts

            return counts, block_max

        end_counts, overall_max = walk_block(stmts)
        max_seen = max(overall_max, (max(end_counts) if end_counts else 0))
        return max_seen

    for path in sorted(runtime_dir.rglob('*.py')):
        if not _is_python_file(path):
            continue

        tree = _parse(path)

        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            if not node.name.startswith('execute_'):
                continue

            max_path_inputs = _max_input_calls_any_path(node.body)
            if max_path_inputs > 1:
                violations.append(
                    Violation(
                        rule='one_intent_one_input',
                        file=_rel(path),
                        detail=f"{node.name}() can emit {max_path_inputs} input_.* calls along a single path (max 1)",
                    )
                )

    return violations


def check_forbidden_hash_digest_evidence(*, root: Path | None = None) -> list[Violation]:
    """Reject hash/digest usage as evidence primitives in runtime code.

    This is a coarse filter: if we ever start needing hashes for non-evidence,
    we should narrow scope explicitly.
    """

    root = root or _repo_root()
    violations: list[Violation] = []

    # Scope: evidence semantics/rules only. (Adapters may hash frames for caching.)
    scan_dirs = [root / 'runtime', root / 'rules']
    needles = ('hashlib', 'md5(', 'sha1(', 'sha256(', 'sha512(')

    for base in scan_dirs:
        if not base.exists():
            continue
        for path in sorted(base.rglob('*.py')):
            if not _is_python_file(path):
                continue
            text = path.read_text(encoding='utf-8', errors='replace')
            for needle in needles:
                if needle in text:
                    violations.append(
                        Violation(
                            rule='hash_digest_evidence_forbidden',
                            file=_rel(path),
                            detail=f"contains '{needle}'",
                        )
                    )

    return violations


def check_engine_one_intent_per_tick_guard_present(*, root: Path | None = None) -> list[Violation]:
    """Ensure the core engine runner still enforces <=1 intent per tick."""

    root = root or _repo_root()
    path = root / 'runtime' / 'runner.py'
    if not path.exists():
        return [Violation(rule='one_intent_per_tick_guard', file=_rel(path), detail='runtime/runner.py missing')]

    text = path.read_text(encoding='utf-8', errors='replace')
    if 'engine produced more than one intent' not in text:
        return [
            Violation(
                rule='one_intent_per_tick_guard',
                file=_rel(path),
                detail="missing guard string 'engine produced more than one intent'",
            )
        ]

    return []


def check_ci_runs_mock_auditor(*, root: Path | None = None) -> list[Violation]:
    """Ensure CI actually runs the auditor in mock mode.

    This makes "CI green" a meaningful signal: if the auditor regresses and exits
    non-zero, the workflow must fail.
    """

    root = root or _repo_root()
    path = root / '.github' / 'workflows' / 'ci.yml'
    if not path.exists():
        return [
            Violation(rule='ci_runs_audit_all_mock', file=_rel(path), detail='missing workflow')
        ]

    text = path.read_text(encoding='utf-8', errors='replace')
    required = [
        'audit-mock:',
        'python tools/audit_all.py',
        'FRBOT_MODE: mock',
    ]

    violations: list[Violation] = []
    for needle in required:
        if needle not in text:
            violations.append(
                Violation(rule='ci_runs_audit_all_mock', file=_rel(path), detail=f"missing '{needle}'")
            )

    # Ensure the audit-mock job is not marked as continue-on-error.
    if 'audit-mock:' in text:
        segment = text.split('audit-mock:', 1)[1]
        m = re.search(r'\n\s{2}[A-Za-z0-9_-]+:\s*\n', segment)
        job_block = segment[: m.start()] if m else segment
        if 'continue-on-error: true' in job_block:
            violations.append(
                Violation(rule='ci_runs_audit_all_mock', file=_rel(path), detail='audit-mock uses continue-on-error: true')
            )

    return violations


def check_no_print_in_runtime(*, root: Path | None = None) -> list[Violation]:
    """Disallow print() in runtime/ and *_entrypoint.py.

    Tooling scripts may print, but runtime code must be evidence-only via logs.
    """

    root = root or _repo_root()
    violations: list[Violation] = []

    candidates: list[Path] = []
    candidates.extend(sorted(root.glob('*_entrypoint.py')))
    runtime_dir = root / 'runtime'
    if runtime_dir.exists():
        candidates.extend(sorted(runtime_dir.rglob('*.py')))

    for path in candidates:
        if not _is_python_file(path):
            continue
        text = path.read_text(encoding='utf-8', errors='replace')
        if 'print(' in text:
            violations.append(
                Violation(rule='no_print_in_runtime', file=_rel(path), detail='contains print(')
            )

    return violations


def run_all_checks(*, root: Path | None = None) -> list[Violation]:
    root = root or _repo_root()

    violations: list[Violation] = []
    violations.extend(check_entrypoints_preflight_before_logger(root=root))
    violations.extend(check_sleep_only_tick_pacing(root=root))
    violations.extend(check_execute_one_input(root=root))
    violations.extend(check_forbidden_hash_digest_evidence(root=root))
    violations.extend(check_engine_one_intent_per_tick_guard_present(root=root))
    violations.extend(check_ci_runs_mock_auditor(root=root))
    violations.extend(check_no_print_in_runtime(root=root))

    return violations


def format_violations(violations: Sequence[Violation]) -> str:
    lines: list[str] = []
    for v in violations:
        lines.append(f"rule={v.rule} file={v.file} detail={v.detail}")
    return '\n'.join(lines)


if __name__ == '__main__':
    v = run_all_checks()
    if v:
        raise SystemExit(format_violations(v))
    raise SystemExit(0)
