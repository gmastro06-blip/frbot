import os
from types import SimpleNamespace

from runtime import cavebot_runner
from contracts.runtime import Waypoint


def test_select_key_inverts_horizontal(monkeypatch: "pytest.MonkeyPatch") -> None:
    # Marker at x=50, waypoint at x=60 -> normally RIGHT
    m = SimpleNamespace(x_px=50, y_px=50)
    wp = Waypoint(waypoint_id="w", x=60, y=50, z=7, radius_px=2, max_ticks=10, waypoint_type="walk", options={})

    # Default behavior: RIGHT
    if 'FRBOT_CAVEBOT_INVERT_HORIZONTAL' in os.environ:
        monkeypatch.delenv('FRBOT_CAVEBOT_INVERT_HORIZONTAL', raising=False)
    k = cavebot_runner._select_key_toward_waypoint(m, wp)
    assert k == 'RIGHT'

    # Inverted behavior
    monkeypatch.setenv('FRBOT_CAVEBOT_INVERT_HORIZONTAL', '1')
    k2 = cavebot_runner._select_key_toward_waypoint(m, wp)
    assert k2 == 'LEFT'
