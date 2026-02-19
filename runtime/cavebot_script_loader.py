"""Typed conversion from models.Script (tile-space) to contracts.runtime.Waypoint (pixel-space).

This module contains the pure conversion logic extracted from the UI layer so that
it can be unit-tested independently and reused from non-UI entry points.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from contracts.runtime import Waypoint as RuntimeWaypoint
from models import Script, Waypoint, WaypointType


@dataclass(frozen=True, slots=True)
class ScriptLoaderParams:
    """Parameters for converting models.Script waypoints to runtime pixel-space waypoints."""

    anchor_x: int
    anchor_y: int
    anchor_z: int
    default_radius_px: int = 2
    default_max_ticks: int = 30


def _is_recorder_route(waypoints: list[Waypoint]) -> bool:
    """Heuristic: detect recorded routes by checking that most coords are small (relative)."""
    if not waypoints:
        return False
    small_count = 0
    for wp in waypoints:
        try:
            xx = abs(int(wp.x))
            yy = abs(int(wp.y))
        except (ValueError, TypeError):
            continue
        if xx <= 2048 and yy <= 2048:
            small_count += 1
    return small_count >= max(1, int(len(waypoints) * 0.8))


def script_to_runtime_waypoints(
    script: Script,
    params: ScriptLoaderParams,
    *,
    force_relative: Optional[bool] = None,
) -> list[RuntimeWaypoint]:
    """Convert models.Script waypoints to contracts.runtime.Waypoint pixel-space list.

    Recorded routes (relative tile coords) are anchored to (anchor_x, anchor_y, anchor_z).
    Legacy absolute tile-space routes are passed through as-is.

    Args:
        script: Source script with models.Waypoint list.
        params: Anchor and defaults for the conversion.
        force_relative: Override route detection. True = treat all coords as relative
                        (recorder route). False = treat as absolute. None = autodetect.

    Returns:
        List of RuntimeWaypoint objects in minimap pixel space, preserving enabled order.
    """
    enabled = [wp for wp in script.waypoints if bool(getattr(wp, "enabled", True))]
    if not enabled:
        return []

    # Determine if coords are relative (recorder route) or absolute tile coords.
    recorder_md = (script.metadata or {}).get("recorder", {})
    has_recorder_metadata = isinstance(recorder_md, dict) and bool(recorder_md)

    if force_relative is not None:
        is_relative = bool(force_relative)
    elif has_recorder_metadata:
        is_relative = True
    else:
        is_relative = _is_recorder_route(enabled)

    # Base tile (first enabled waypoint) for relative→absolute conversion.
    base_x = 0
    base_y = 0
    base_z = 0
    if is_relative:
        first = enabled[0]
        try:
            base_x = int(first.x)
            base_y = int(first.y)
            base_z = int(first.z)
        except (ValueError, TypeError):
            base_x, base_y, base_z = 0, 0, 0

    result: list[RuntimeWaypoint] = []
    result_idx = 0
    for wp in script.waypoints:
        if not bool(getattr(wp, "enabled", True)):
            continue

        opts: dict[str, Any] = dict(getattr(wp, "options", {}) or {})
        try:
            radius_px = max(0, int(opts.get("radius_px", params.default_radius_px)))
        except (ValueError, TypeError):
            radius_px = int(params.default_radius_px)
        try:
            max_ticks = max(1, int(opts.get("max_ticks", params.default_max_ticks)))
        except (ValueError, TypeError):
            max_ticks = int(params.default_max_ticks)

        x_val = int(wp.x)
        y_val = int(wp.y)
        z_val = int(wp.z)
        if is_relative:
            x_val = int(params.anchor_x) + (x_val - base_x)
            y_val = int(params.anchor_y) + (y_val - base_y)
            z_val = int(params.anchor_z) + (z_val - base_z)

        wp_type = str(getattr(wp, "type", WaypointType.WALK.value) or WaypointType.WALK.value)

        result.append(
            RuntimeWaypoint(
                waypoint_id=f"wp_{result_idx}",
                x=int(x_val),
                y=int(y_val),
                z=int(z_val),
                radius_px=int(radius_px),
                max_ticks=int(max_ticks),
                waypoint_type=wp_type,
                options=opts,
            )
        )
        result_idx += 1

    return result
