from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from contracts.runtime import Waypoint


@dataclass(frozen=True, slots=True)
class CavebotIntent:
    key: str
    waypoint_id: str


@dataclass(frozen=True, slots=True)
class CavebotAbort:
    reason: str


@dataclass(frozen=True, slots=True)
class CavebotTickInput:
    waypoint: Waypoint
    ticks_in_waypoint: int
    attempts_used: int
    max_attempts_per_waypoint: int
    max_ticks_per_waypoint: int


def tick(tick_input: CavebotTickInput) -> tuple[Optional[CavebotIntent], Optional[CavebotAbort]]:
    """Pure cavebot rule engine.

    Does not read frames. Does not mutate state. Does not assume success.

    Returns either (intent, None) or (None, abort).
    """

    wp = tick_input.waypoint

    if tick_input.attempts_used >= tick_input.max_attempts_per_waypoint:
        return None, CavebotAbort('cavebot_waypoint_stuck')

    if tick_input.ticks_in_waypoint >= tick_input.max_ticks_per_waypoint:
        return None, CavebotAbort('cavebot_waypoint_stuck')

    expected = str(wp.expected_direction).strip().upper()
    if expected == 'N':
        key = 'UP'
    elif expected == 'S':
        key = 'DOWN'
    elif expected == 'E':
        key = 'RIGHT'
    elif expected == 'W':
        key = 'LEFT'
    else:
        return None, CavebotAbort('cavebot_no_progress')

    return CavebotIntent(key=key, waypoint_id=str(wp.waypoint_id)), None
