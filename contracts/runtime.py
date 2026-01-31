from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .errors import ContractViolation
from .capture import CaptureStatus
from .input import InputStatus


class RuntimeState(str, Enum):
    INIT = 'INIT'
    PREFLIGHT = 'PREFLIGHT'
    READY = 'READY'
    RUNNING = 'RUNNING'
    ABORTED = 'ABORTED'


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """Only supported modes:

    - real: aborts (no real adapters are implemented by design)
    - mock: deterministic mock adapters
    """

    mode: str
    tick_hz: float = 20.0

    def __post_init__(self) -> None:
        mode = self.mode.strip().lower()
        if mode not in {'real', 'mock'}:
            raise ContractViolation(f'Unsupported mode: {self.mode!r}')
        if self.tick_hz <= 0 or self.tick_hz > 200:
            raise ContractViolation('tick_hz must be in (0, 200]')


@dataclass(slots=True)
class RuntimeTelemetry:
    tick_count: int = 0
    last_frame_ts_ns: int = 0
    last_capture_age_ms: int = 0
    last_tick_valid: bool = False


@dataclass(slots=True)
class RuntimeStatus:
    state: RuntimeState = RuntimeState.INIT
    reason: str = ''


@dataclass(slots=True)
class RuntimeContext:
    config: RuntimeConfig
    status: RuntimeStatus

    telemetry: RuntimeTelemetry

    capture: Optional[CaptureStatus] = None
    input: Optional[InputStatus] = None
