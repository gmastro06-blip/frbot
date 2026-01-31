from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from runtime.looting import LootIntent


@dataclass(frozen=True, slots=True)
class LootingAbort:
    reason: str


@dataclass(frozen=True, slots=True)
class LootingTickInput:
    mode: Literal['premium', 'free']
    container_open: bool
    quick_loot_key: str
    ticks_used: int
    attempts_used: int
    max_ticks: int
    max_attempts: int


def select_looting_intent(tick_input: LootingTickInput) -> tuple[Optional[LootIntent], Optional[LootingAbort]]:
    """Pure looting rule engine.

    Does not read frames. Does not mutate state. Never assumes success.

    Returns either (intent, None) or (None, abort).
    """

    if tick_input.attempts_used >= int(tick_input.max_attempts):
        return None, LootingAbort('looting_stuck')

    if tick_input.ticks_used >= int(tick_input.max_ticks):
        return None, LootingAbort('looting_stuck')

    mode = str(tick_input.mode).strip().lower()

    if mode == 'premium':
        key = str(tick_input.quick_loot_key).strip()
        if not key:
            return None, LootingAbort('looting_invalid_config')
        return LootIntent(kind='press_key', key=key, mode='premium'), None

    # free mode (manual): tick alternates between opening container and taking an item
    if not bool(tick_input.container_open):
        return LootIntent(kind='click', mode='free'), None

    return LootIntent(kind='click', mode='free'), None
