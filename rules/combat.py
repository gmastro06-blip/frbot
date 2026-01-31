from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from contracts.combat import CombatEvidenceExpectation, CombatIntent, CombatEngineOutput
from contracts.runtime import TargetState


def select_combat_intent(
    *,
    target: TargetState,
    attack_key: str,
    target_hp_decrease_min: float,
) -> CombatEngineOutput:
    """Pure combat rule.

    This mode NEVER selects a target. It requires a verified locked target.

    If target is not locked/identified -> abort combat_invalid_state.
    """

    if not bool(target.locked):
        return CombatEngineOutput(intent=None, abort_reason='combat_target_not_locked')
    if not (target.target_name and str(target.target_name).strip()):
        return CombatEngineOutput(intent=None, abort_reason='combat_target_not_locked')
    if not (target.target_id and str(target.target_id).strip()):
        return CombatEngineOutput(intent=None, abort_reason='combat_target_not_locked')

    if not str(attack_key or '').strip():
        return CombatEngineOutput(intent=None, abort_reason='combat_ambiguous_result')

    return CombatEngineOutput(
        intent=CombatIntent(
            key=str(attack_key),
            expected=CombatEvidenceExpectation(target_hp_decrease_min=float(target_hp_decrease_min)),
        )
    )
