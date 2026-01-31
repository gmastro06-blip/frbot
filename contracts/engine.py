from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class EngineIntent(str, Enum):
    NOOP = 'NOOP'


@dataclass(frozen=True, slots=True)
class TickInput:
    now_ts_ns: int
    frame_ts_ns: int
    capture_age_ms: int
    max_capture_age_ms: int


@dataclass(frozen=True, slots=True)
class EngineOutput:
    intent: EngineIntent
    abort_reason: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.abort_reason is None
