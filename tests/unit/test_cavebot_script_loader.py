"""Unit tests for runtime/cavebot_script_loader.py."""
from __future__ import annotations

import pytest

from models import Script, Waypoint, WaypointType, now_iso
from runtime.cavebot_script_loader import ScriptLoaderParams, script_to_runtime_waypoints


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wp(x: int, y: int, z: int = 7, *, wp_type: str = "walk", enabled: bool = True, options: dict | None = None) -> Waypoint:
    return Waypoint(
        type=str(wp_type),
        x=int(x),
        y=int(y),
        z=int(z),
        options=dict(options or {}),
        enabled=bool(enabled),
        created_at=now_iso(),
    )


def _script(*waypoints: Waypoint, metadata: dict | None = None) -> Script:
    return Script(
        name="test_script",
        enabled=True,
        run_to_target=False,
        waypoints=list(waypoints),
        metadata=dict(metadata or {}),
    )


def _recorder_script(*waypoints: Waypoint) -> Script:
    """Script marked as a recorder route via metadata."""
    return _script(*waypoints, metadata={"recorder": {"default_z": 0, "simplify_straight_every": 3}})


_PARAMS = ScriptLoaderParams(anchor_x=60, anchor_y=40, anchor_z=7)


# ---------------------------------------------------------------------------
# Empty / disabled
# ---------------------------------------------------------------------------


def test_empty_script_returns_empty_list() -> None:
    result = script_to_runtime_waypoints(_script(), _PARAMS)
    assert result == []


def test_all_disabled_waypoints_returns_empty_list() -> None:
    script = _script(_wp(0, 0, enabled=False), _wp(10, 10, enabled=False))
    result = script_to_runtime_waypoints(script, _PARAMS)
    assert result == []


def test_disabled_waypoints_skipped_in_middle() -> None:
    script = _recorder_script(_wp(0, 0), _wp(5, 0, enabled=False), _wp(10, 0))
    result = script_to_runtime_waypoints(script, _PARAMS)
    assert len(result) == 2
    assert result[0].x == 60
    assert result[1].x == 70


# ---------------------------------------------------------------------------
# Recorder routes (relative coords, small values)
# ---------------------------------------------------------------------------


def test_recorder_route_via_metadata_anchors_to_anchor() -> None:
    """Waypoints from a recorded route are offset from anchor; first wp is at anchor."""
    script = _recorder_script(_wp(0, 0), _wp(-2, -1), _wp(-4, -1))
    result = script_to_runtime_waypoints(script, _PARAMS)

    assert len(result) == 3
    assert (result[0].x, result[0].y) == (60, 40)    # anchor
    assert (result[1].x, result[1].y) == (58, 39)    # anchor + delta
    assert (result[2].x, result[2].y) == (56, 39)


def test_recorder_route_z_carried_from_anchor() -> None:
    script = _recorder_script(_wp(0, 0, 0), _wp(1, 0, 0))
    result = script_to_runtime_waypoints(script, _PARAMS)
    assert result[0].z == 7   # anchor_z
    assert result[1].z == 7


def test_recorder_route_non_zero_base_tile_normalised() -> None:
    """If the first waypoint is not (0,0,0) the delta is still from the base."""
    script = _recorder_script(_wp(5, 3), _wp(7, 3), _wp(9, 4))
    result = script_to_runtime_waypoints(script, _PARAMS)
    # base is (5,3), anchor is (60,40)
    assert (result[0].x, result[0].y) == (60, 40)
    assert (result[1].x, result[1].y) == (62, 40)
    assert (result[2].x, result[2].y) == (64, 41)


def test_heuristic_autodetect_small_coords_as_recorder() -> None:
    """Script without recorder metadata but with small coords is auto-detected as relative."""
    script = _script(_wp(0, 0), _wp(-3, 1))   # no recorder metadata
    result = script_to_runtime_waypoints(script, _PARAMS)
    assert (result[0].x, result[0].y) == (60, 40)
    assert (result[1].x, result[1].y) == (57, 41)


# ---------------------------------------------------------------------------
# Absolute (tile-space) routes
# ---------------------------------------------------------------------------


def test_absolute_coords_passed_through() -> None:
    """World tile coords (>2048) are passed through without offset."""
    script = _script(_wp(32350, 32225, 7), _wp(32360, 32220, 7))
    result = script_to_runtime_waypoints(script, _PARAMS)
    assert (result[0].x, result[0].y) == (32350, 32225)
    assert (result[1].x, result[1].y) == (32360, 32220)


# ---------------------------------------------------------------------------
# force_relative override
# ---------------------------------------------------------------------------


def test_force_relative_true_overrides_absolute_coords() -> None:
    """force_relative=True treats large coords as relative offsets from anchor."""
    script = _script(_wp(0, 0), _wp(5, 0))   # no metadata
    params = ScriptLoaderParams(anchor_x=100, anchor_y=80, anchor_z=7)
    result = script_to_runtime_waypoints(script, params, force_relative=True)
    assert (result[0].x, result[0].y) == (100, 80)
    assert (result[1].x, result[1].y) == (105, 80)


def test_force_relative_false_overrides_recorder_metadata() -> None:
    """force_relative=False forces absolute mode even for recorder routes."""
    script = _recorder_script(_wp(0, 0), _wp(5, 0))
    result = script_to_runtime_waypoints(script, _PARAMS, force_relative=False)
    # Not offset from anchor — passed through as-is
    assert (result[0].x, result[0].y) == (0, 0)
    assert (result[1].x, result[1].y) == (5, 0)


# ---------------------------------------------------------------------------
# radius_px / max_ticks from options
# ---------------------------------------------------------------------------


def test_radius_px_from_options() -> None:
    script = _recorder_script(_wp(0, 0, options={"radius_px": 5}))
    result = script_to_runtime_waypoints(script, _PARAMS)
    assert result[0].radius_px == 5


def test_max_ticks_from_options() -> None:
    script = _recorder_script(_wp(0, 0, options={"max_ticks": 100}))
    result = script_to_runtime_waypoints(script, _PARAMS)
    assert result[0].max_ticks == 100


def test_default_radius_px_applied() -> None:
    params = ScriptLoaderParams(anchor_x=60, anchor_y=40, anchor_z=7, default_radius_px=3)
    script = _recorder_script(_wp(0, 0))
    result = script_to_runtime_waypoints(script, params)
    assert result[0].radius_px == 3


def test_default_max_ticks_applied() -> None:
    params = ScriptLoaderParams(anchor_x=60, anchor_y=40, anchor_z=7, default_max_ticks=50)
    script = _recorder_script(_wp(0, 0))
    result = script_to_runtime_waypoints(script, params)
    assert result[0].max_ticks == 50


# ---------------------------------------------------------------------------
# WaypointType preservation (incl. new types)
# ---------------------------------------------------------------------------


def test_walk_type_preserved() -> None:
    script = _recorder_script(_wp(0, 0, wp_type=WaypointType.WALK.value))
    result = script_to_runtime_waypoints(script, _PARAMS)
    assert result[0].waypoint_type == "walk"


def test_call_npc_type_preserved() -> None:
    """CALL_NPC waypoints are preserved and correctly positioned."""
    script = _recorder_script(
        _wp(0, 0),
        _wp(2, 0, wp_type=WaypointType.CALL_NPC.value, options={"call": "talk_npc", "payload": "hi"}),
    )
    result = script_to_runtime_waypoints(script, _PARAMS)
    assert result[1].waypoint_type == "call_npc"
    assert result[1].options["call"] == "talk_npc"


def test_conditional_jump_type_preserved() -> None:
    """CONDITIONAL_JUMP waypoints are preserved."""
    script = _recorder_script(
        _wp(0, 0),
        _wp(2, 0, wp_type=WaypointType.CONDITIONAL_JUMP.value,
            options={"call": "conditional_jump_script_options", "payload": "..."}),
    )
    result = script_to_runtime_waypoints(script, _PARAMS)
    assert result[1].waypoint_type == "conditional_jump"


def test_use_ladder_type_preserved() -> None:
    script = _script(_wp(32400, 32217, 7, wp_type=WaypointType.USE_LADDER.value))
    result = script_to_runtime_waypoints(script, _PARAMS)
    assert result[0].waypoint_type == "use_ladder"


def test_rope_type_preserved() -> None:
    script = _script(_wp(32400, 32217, 7, wp_type=WaypointType.ROPE.value))
    result = script_to_runtime_waypoints(script, _PARAMS)
    assert result[0].waypoint_type == "rope"


# ---------------------------------------------------------------------------
# Options carried through unchanged
# ---------------------------------------------------------------------------


def test_options_carried_through() -> None:
    script = _recorder_script(_wp(0, 0, options={"action_kind": "call_npc", "custom": 42}))
    result = script_to_runtime_waypoints(script, _PARAMS)
    assert result[0].options["action_kind"] == "call_npc"
    assert result[0].options["custom"] == 42
