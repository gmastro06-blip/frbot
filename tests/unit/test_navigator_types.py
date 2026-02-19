from __future__ import annotations

from runtime.navigator_types import validate_waypoints, NavigatorConfig, NavigatorState


def test_validate_waypoints_ok():
    waypoints = [{'x': 1, 'y': 2}, {'x': 0, 'y': 5}]
    errs = validate_waypoints(waypoints)
    assert errs == []


def test_validate_waypoints_bad():
    waypoints = [None, {'x': -1, 'y': 'a'}, {'z': 1}]
    errs = validate_waypoints(waypoints)
    assert len(errs) >= 1


def test_dataclass_defaults():
    cfg = NavigatorConfig()
    st = NavigatorState()
    assert cfg.max_ticks_per_waypoint > 0
    assert st.current_waypoint_index == 0
