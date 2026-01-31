from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from .runtime import Tile


@dataclass(frozen=True, slots=True)
class EngineInput:
    now_ts_ms: int
    capture_age_ms: int
    max_capture_age_ms: int

    tick_count: int
    current_position: Tile
    target_tile: Optional[Tile]
    last_positions: tuple[Tile, ...] = ()


@dataclass(frozen=True, slots=True)
class EngineTelemetry:
    tick_count: int
    last_capture_age_ms: int
    last_tick_valid: bool


@dataclass(frozen=True, slots=True)
class IntentMove:
    direction: Literal['up', 'down', 'left', 'right']
    reason: Literal['cavebot']


@dataclass(frozen=True, slots=True)
class EngineOutput:
    intents: tuple[IntentMove, ...] = ()
    telemetry: Optional[EngineTelemetry] = None
    abort_reason: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.abort_reason is None
