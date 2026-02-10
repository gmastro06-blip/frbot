from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class WaypointType(str, Enum):
    WALK = "walk"
    WALK_IGNORE = "walk_ignore"
    SINGLE_MOVE = "single_move"
    MOVE_UP = "move_up"
    MOVE_DOWN = "move_down"
    USE_RIGHT_CLICK = "use_right_click"
    OPEN_DOOR = "open_door"
    USE_LADDER = "use_ladder"
    ROPE = "rope"
    REFILL = "refill"
    TRAVEL = "travel"
    DEPOSIT = "deposit"
    TRADE = "trade"

    @classmethod
    def values(cls) -> list[str]:
        return [e.value for e in cls]


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@dataclass(slots=True)
class Waypoint:
    type: str
    x: int
    y: int
    z: int
    options: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    created_at: str = field(default_factory=now_iso)


@dataclass(slots=True)
class Script:
    name: str
    enabled: bool = True
    run_to_target: bool = False
    waypoints: list[Waypoint] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
