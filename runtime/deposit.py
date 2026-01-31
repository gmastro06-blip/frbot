from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True, slots=True)
class DepositIntent:
    key: str


@dataclass(frozen=True, slots=True)
class DepositAbort:
    reason: str


@dataclass(frozen=True, slots=True)
class DepositTickInput:
    deposit_key: str
    ticks_used: int
    attempts_used: int
    max_ticks: int
    max_attempts: int


def tick(tick_input: DepositTickInput) -> tuple[Optional[DepositIntent], Optional[DepositAbort]]:
    """Pure deposit rule engine.

    No IO. Emits at most one intent per tick.
    """

    if tick_input.attempts_used >= int(tick_input.max_attempts):
        return None, DepositAbort('deposit_timeout')

    if tick_input.ticks_used >= int(tick_input.max_ticks):
        return None, DepositAbort('deposit_timeout')

    key = str(tick_input.deposit_key).strip()
    if not key:
        return None, DepositAbort('deposit_timeout')

    return DepositIntent(key=key), None
