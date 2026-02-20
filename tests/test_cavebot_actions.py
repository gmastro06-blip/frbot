import os
from types import SimpleNamespace

import pytest

from runtime.cavebot_runner import (
    _special_action_key_for_waypoint,
    _is_special_action_waypoint,
    _requires_movement_for_waypoint,
)


def make_wp(wp_type: str = 'walk', options: dict | None = None):
    return SimpleNamespace(waypoint_type=wp_type, options=dict(options or {}))


def test_open_door_is_special_and_has_key(monkeypatch):
    monkeypatch.setenv('FRBOT_OPEN_DOOR_KEY', 'F7')
    wp = make_wp('open_door', {})
    assert _is_special_action_waypoint(wp)
    key = _special_action_key_for_waypoint(wp)
    assert key == 'F7'


def test_requires_movement_default_for_open_door():
    wp = make_wp('open_door', {})
    assert _requires_movement_for_waypoint(wp) is False


def test_requires_movement_override_true():
    wp = make_wp('open_door', {'requires_movement': True})
    assert _requires_movement_for_waypoint(wp) is True


def test_requires_movement_for_rope():
    wp = make_wp('rope', {})
    assert _requires_movement_for_waypoint(wp) is True


if __name__ == '__main__':
    pytest.main([__file__])
