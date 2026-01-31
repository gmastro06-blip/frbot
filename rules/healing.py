from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from contracts.healing import HealEvidenceExpectation, HealIntent


@dataclass(frozen=True, slots=True)
class HealingRuleResult:
    intent: Optional[HealIntent] = None
    abort_reason: Optional[str] = None


def select_heal_intent(*, hp: float, mp: float, hp_threshold: float, mp_min: float, mp_cost: float, heal_key: str, hp_increase_min: float) -> HealingRuleResult:
    """Pure healing rule.

    Emits a HealIntent only when:
    - hp <= threshold
    - mp >= (mp_min + mp_cost)

    Does not assume cooldown state or success.
    """

    if hp < 0.0 or hp > 1.0 or mp < 0.0 or mp > 1.0:
        return HealingRuleResult(abort_reason='hp_mp_unreadable')

    if hp > float(hp_threshold):
        return HealingRuleResult()

    if mp < float(mp_min) + float(mp_cost):
        return HealingRuleResult()

    return HealingRuleResult(
        intent=HealIntent(
            key=str(heal_key),
            expected=HealEvidenceExpectation(hp_increase_min=float(hp_increase_min), require_cooldown_visible=True, require_feedback_visible=False),
        )
    )
