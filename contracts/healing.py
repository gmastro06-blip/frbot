from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True, slots=True)
class HealEvidenceExpectation:
    hp_increase_min: float
    require_cooldown_visible: bool = True
    require_feedback_visible: bool = False


@dataclass(frozen=True, slots=True)
class HealIntent:
    key: str
    expected: HealEvidenceExpectation


@dataclass(frozen=True, slots=True)
class HealingEngineOutput:
    intent: Optional[HealIntent] = None
    abort_reason: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.abort_reason is None
