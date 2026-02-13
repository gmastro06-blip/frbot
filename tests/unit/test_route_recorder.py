from __future__ import annotations

from runtime.route_recorder import RouteRecordingSession


def test_route_recorder_records_first_tile_and_direction_changes() -> None:
    rec = RouteRecordingSession(script_name='r1', default_z=7, simplify_straight_every=3)

    assert rec.record_tile(10, 10, 7) is True
    assert rec.record_tile(11, 10, 7) is True
    assert rec.record_tile(12, 10, 7) is False
    assert rec.record_tile(12, 11, 7) is True

    assert len(rec.waypoints) == 3
    assert rec.waypoints[0].x == 10 and rec.waypoints[0].y == 10
    assert rec.waypoints[1].x == 11 and rec.waypoints[1].y == 10
    assert rec.waypoints[2].x == 12 and rec.waypoints[2].y == 11


def test_route_recorder_marks_actions_on_last_position() -> None:
    rec = RouteRecordingSession(script_name='r2', default_z=7, simplify_straight_every=2)
    rec.record_tile(5, 6, 7)

    wp_ladder = rec.mark_action('ladder')
    wp_rope = rec.mark_action('rope')
    wp_hole = rec.mark_action('open_hole')

    assert wp_ladder.type == 'use_ladder'
    assert wp_rope.type == 'rope'
    assert wp_hole.type == 'use_right_click'
    assert wp_hole.options.get('interaction') == 'open_hole'


def test_route_recorder_build_script_contains_waypoints() -> None:
    rec = RouteRecordingSession(script_name='my_route', default_z=7, simplify_straight_every=2)
    rec.record_tile(0, 0, 7)
    rec.record_tile(1, 0, 7)
    rec.mark_action('rope')

    script = rec.build_script()

    assert script.name == 'my_route'
    assert len(script.waypoints) >= 2
    assert script.metadata.get('recorder') is not None
