from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from contracts.runtime import BattleListEntry, TargetState
from contracts.targeting import IntentTarget, TargetEvidenceExpectation


@dataclass(frozen=True, slots=True)
class TargetingRuleResult:
    intent: Optional[IntentTarget] = None
    abort_reason: Optional[str] = None


def select_targeting_intent(current: TargetState, entries: tuple[BattleListEntry, ...]) -> TargetingRuleResult:
    """Pure targeting rule.

    Invariants:
    - Never assumes selection success.
    - Returns None when no safe candidate exists.
    """

    # 1) If already locked and valid -> NOOP.
    if current.locked and current.target_name:
        for e in entries:
            if e.highlighted and e.name and e.name == current.target_name:
                return TargetingRuleResult()

    # 2) Filter candidates.
    candidates = tuple(
        e
        for e in entries
        if bool(e.name) and e.is_attackable is True and e.hp_bar_visible is True
    )

    # 3) If 0 candidates -> NOOP.
    if not candidates:
        return TargetingRuleResult()

    # 4) If >1 candidates -> deterministic selection.
    chosen = min(candidates, key=lambda e: int(e.row_index))

    # 5) Emit intent.
    return TargetingRuleResult(
        intent=IntentTarget(
            target_name=str(chosen.name),
            battle_list_row_index=int(chosen.row_index),
            expected_evidence=TargetEvidenceExpectation(
                battle_list_row_highlighted=True,
                target_frame_visible=True,
                target_hp_bar_present=True,
            ),
        )
    )
