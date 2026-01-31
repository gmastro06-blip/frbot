from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True, slots=True)
class TargetEvidenceExpectation:
    battle_list_row_highlighted: bool = True
    target_frame_visible: bool = True
    target_hp_bar_present: bool = True


@dataclass(frozen=True, slots=True)
class IntentTarget:
    target_name: str
    battle_list_row_index: int
    expected_evidence: TargetEvidenceExpectation


@dataclass(frozen=True, slots=True)
class TargetingEngineOutput:
    intent: Optional[IntentTarget] = None
    abort_reason: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.abort_reason is None
