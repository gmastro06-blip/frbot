from __future__ import annotations

from pathlib import Path

import ci_guardrails


def test_execute_functions_emit_at_most_one_input() -> None:
    root = Path(__file__).resolve().parents[1]
    violations = ci_guardrails.check_execute_one_input(root=root)
    assert not violations, "one_intent_one_input violations:\n" + ci_guardrails.format_violations(violations)


def test_engine_one_intent_per_tick_guard_present() -> None:
    root = Path(__file__).resolve().parents[1]
    violations = ci_guardrails.check_engine_one_intent_per_tick_guard_present(root=root)
    assert not violations, "one_intent_per_tick_guard violations:\n" + ci_guardrails.format_violations(violations)
