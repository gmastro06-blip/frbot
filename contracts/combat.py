from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True, slots=True)
class CombatEvidenceExpectation:
    """Semantic evidence required to accept an attack.

    Exactly one attack is executed per intent. Evidence must be semantic:
    - target HP decreases (preferred)
    - OR damage feedback marker becomes visible

    Cooldown visibility is a *precondition*, not combat evidence.
    """

    target_hp_decrease_min: float
    require_damage_feedback: bool = False


@dataclass(frozen=True, slots=True)
class CombatIntent:
    key: str
    expected: CombatEvidenceExpectation


@dataclass(frozen=True, slots=True)
class CombatEngineOutput:
    intent: Optional[CombatIntent] = None
    abort_reason: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.abort_reason is None
