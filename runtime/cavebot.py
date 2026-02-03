from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from contracts.runtime import MinimapMarker, Waypoint


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
    marker_before: MinimapMarker
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

    # Direction is computed from marker->waypoint vector.
    dx = int(wp.x) - int(tick_input.marker_before.x_px)
    dy = int(wp.y) - int(tick_input.marker_before.y_px)
    if dx == 0 and dy == 0:
        return None, CavebotAbort('cavebot_no_progress')

    if abs(dx) >= abs(dy):
        key = 'RIGHT' if dx > 0 else 'LEFT'
    else:
        key = 'DOWN' if dy > 0 else 'UP'

    return CavebotIntent(key=key, waypoint_id=str(wp.waypoint_id)), None
