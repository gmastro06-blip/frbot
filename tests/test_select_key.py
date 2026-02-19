import os

from contracts.runtime import MinimapMarker, Waypoint
from runtime.cavebot_runner import _select_key_toward_waypoint


def make_wp(x: int, y: int) -> Waypoint:
    return Waypoint(waypoint_id='w', x=int(x), y=int(y), z=0, radius_px=5, max_ticks=10)


def test_select_key_horizontal_prefers_right():
    m = MinimapMarker(x_px=10, y_px=10, pixel_count=5)
    wp = make_wp(15, 12)
    assert _select_key_toward_waypoint(m, wp) in {'RIGHT', 'LEFT'}


def test_select_key_prefers_horizontal_when_dx_ge_dy():
    m = MinimapMarker(x_px=10, y_px=10, pixel_count=5)
    wp = make_wp(20, 12)  # dx=10, dy=2 -> horizontal
    assert _select_key_toward_waypoint(m, wp) in {'RIGHT'}


def test_select_key_invert_horizontal_env():
    # Ensure inversion flips horizontal mapping
    os.environ['FRBOT_CAVEBOT_INVERT_HORIZONTAL'] = '1'
    try:
        m = MinimapMarker(x_px=10, y_px=10, pixel_count=5)
        wp = make_wp(20, 10)
        k = _select_key_toward_waypoint(m, wp)
        assert k in {'LEFT', 'RIGHT'}
    finally:
        os.environ.pop('FRBOT_CAVEBOT_INVERT_HORIZONTAL', None)
