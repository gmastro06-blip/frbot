from __future__ import annotations

from typing import Any

from runtime.error_policy import should_reraise


def make_waypoint_payload(waypoint: Any) -> dict[str, Any]:
    """Return a JSON-serializable dict for a waypoint-like object.

    Accepts objects with attributes: waypoint_id, x, y, z, radius_px, max_ticks,
    waypoint_type, options. Falls back to Tile-like objects with x,y,z.
    """
    if waypoint is None:
        return {}
    try:
        wid = str(getattr(waypoint, 'waypoint_id', '') or '')
    except Exception:
        if should_reraise():
            raise
        wid = ''
    if not wid:
        try:
            wid = f"{int(getattr(waypoint, 'x', 0))},{int(getattr(waypoint, 'y', 0))},{int(getattr(waypoint, 'z', 0))}"
        except Exception:
            if should_reraise():
                raise
            wid = ''

    try:
        x = int(getattr(waypoint, 'x', 0))
    except Exception:
        if should_reraise():
            raise
        x = 0
    try:
        y = int(getattr(waypoint, 'y', 0))
    except Exception:
        if should_reraise():
            raise
        y = 0
    try:
        z = int(getattr(waypoint, 'z', 0))
    except Exception:
        if should_reraise():
            raise
        z = 0
    try:
        radius_px = int(getattr(waypoint, 'radius_px', 0))
    except Exception:
        if should_reraise():
            raise
        radius_px = 0
    try:
        max_ticks = int(getattr(waypoint, 'max_ticks', 0))
    except Exception:
        if should_reraise():
            raise
        max_ticks = 0
    try:
        waypoint_type = str(getattr(waypoint, 'waypoint_type', '') or 'walk')
    except Exception:
        if should_reraise():
            raise
        waypoint_type = 'walk'
    try:
        options = dict(getattr(waypoint, 'options', {}) or {})
    except Exception:
        if should_reraise():
            raise
        options = {}

    return {
        'waypoint_id': wid,
        'x': x,
        'y': y,
        'z': z,
        'radius_px': radius_px,
        'max_ticks': max_ticks,
        'waypoint_type': waypoint_type,
        'options': options,
    }
