from __future__ import annotations

from pathlib import Path

import ci_guardrails


def test_ci_guardrails_no_violations() -> None:
    violations = ci_guardrails.run_all_checks(root=Path(__file__).resolve().parents[1])
    assert not violations, "CI guardrails violations:\n" + ci_guardrails.format_violations(violations)
