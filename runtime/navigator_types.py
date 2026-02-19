from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Mapping


@dataclass
class NavigatorConfig:
    """Lightweight navigator configuration for runtime guards.

    These defaults are conservative and intended to be tuned by higher-level
    callers. Keep this file minimal and dependency-free so it can be imported
    widely without side-effects.
    """

    max_ticks_per_waypoint: int = 300
    max_attempts_per_action: int = 3
    stuck_threshold_ticks: int = 50


@dataclass
class NavigatorState:
    current_waypoint_index: int = 0
    ticks_on_waypoint: int = 0
    attempts_on_action: int = 0
    last_action: Optional[str] = None
    last_position: Optional[Tuple[int, int]] = None


@dataclass
class TelemetrySample:
    ts: float
    progress: float = 0.0
    distance: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)


def validate_waypoints(waypoints: Sequence[Mapping[str, Any]]) -> List[str]:
    """Validate a sequence of waypoint-like mappings.

    Returns a list of human-readable error strings. An empty list indicates
    the waypoints look valid. This function is intentionally conservative and
    used as a pre-flight guard before the navigator consumes the data.
    """
    errors: List[str] = []

    if waypoints is None:
        errors.append("waypoints: missing (None)")
        return errors

    if not isinstance(waypoints, Iterable):
        errors.append(f"waypoints: not iterable (type={type(waypoints).__name__})")
        return errors

    for i, w in enumerate(waypoints):
        if not isinstance(w, dict):
            errors.append(f"waypoint[{i}]: expected mapping, got {type(w).__name__}")
            continue
        if 'x' not in w or 'y' not in w:
            errors.append(f"waypoint[{i}]: missing 'x'/'y' keys")
            continue
        try:
            x = int(w['x'])
            y = int(w['y'])
        except Exception:
            errors.append(f"waypoint[{i}]: 'x' and 'y' must be integers")
            continue
        if x < 0 or y < 0:
            errors.append(f"waypoint[{i}]: coordinates must be non-negative (x={x}, y={y})")

    return errors
