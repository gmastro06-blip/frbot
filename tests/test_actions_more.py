import os
from types import SimpleNamespace

from runtime.route_recorder import WaypointRecorder
from runtime.cavebot_runner import _special_action_key_for_waypoint


def test_waypoint_recorder_send_one_input_click(tmp_path):
    class DummyInput:
        def __init__(self):
            self.clicked = None
            self.pressed = []
        def click(self, x, y):
            self.clicked = (int(x), int(y))
        def press_key(self, key):
            self.pressed.append(str(key))

    dummy = DummyInput()
    # minimal stubs for required args
    capture = SimpleNamespace()
    binding = SimpleNamespace()
    marker_cfg = SimpleNamespace()

    wr = WaypointRecorder(capture=capture, input_adapter=dummy, binding=binding, marker_cfg=marker_cfg, out_dir=str(tmp_path))
    wr._send_one_input('click:12,34')
    assert dummy.clicked == (12, 34)


def test_waypoint_recorder_send_one_input_key(tmp_path):
    class DummyInput:
        def __init__(self):
            self.clicked = None
            self.pressed = []
        def click(self, x, y):
            self.clicked = (int(x), int(y))
        def press_key(self, key):
            self.pressed.append(str(key))

    dummy = DummyInput()
    capture = SimpleNamespace()
    binding = SimpleNamespace()
    marker_cfg = SimpleNamespace()

    wr = WaypointRecorder(capture=capture, input_adapter=dummy, binding=binding, marker_cfg=marker_cfg, out_dir=str(tmp_path))
    wr._send_one_input('F9')
    assert 'F9' in dummy.pressed


def test_use_right_click_maps_to_shovel_key(monkeypatch):
    monkeypatch.setenv('FRBOT_SHOVEL_KEY', 'F9')
    wp = SimpleNamespace(waypoint_type='use_right_click', options={'action_kind': 'shovel'})
    key = _special_action_key_for_waypoint(wp)
    assert key == 'F9'
