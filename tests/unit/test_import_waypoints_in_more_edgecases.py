import tempfile
from pathlib import Path

from storage import parse_in_to_script, load_script
from models import WaypointType


def test_malformed_lines_are_ignored():
    content = '''
label malformed
this is not valid
node (1,2 7)
node (5,6,7)
'''
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "mal.in"
        p.write_text(content, encoding='utf-8')
        script = parse_in_to_script(p)
        # only the well-formed node should be parsed
        assert len(script.waypoints) == 1
        assert script.waypoints[0].x == 5


def test_unbalanced_parens_handling():
    content = '''
label unbalanced
node (1,2,7
node (3,4,7)
'''
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "ub.in"
        p.write_text(content, encoding='utf-8')
        script = parse_in_to_script(p)
        assert len(script.waypoints) == 1
        assert script.waypoints[0].x == 3


def test_stand_parsed_and_options_true():
    content = 'label stand_test\nstand (10, 11, 7)\n'
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "stand.in"
        p.write_text(content, encoding='utf-8')
        script = parse_in_to_script(p)
        assert len(script.waypoints) == 1
        assert script.waypoints[0].options.get('stand') is True


def test_shovel_and_pick_variants():
    content = '''
shovel(32910,32515,7)
 pick (1,2,3)
open_door(2,3,4)
'''
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "verbs.in"
        p.write_text(content, encoding='utf-8')
        script = parse_in_to_script(p)
        kinds = [wp.options.get('action_kind') for wp in script.waypoints]
        assert 'shovel' in kinds
        assert 'pick' in kinds
        # open_door should produce OPEN_DOOR waypoint type
        assert any(wp.type == WaypointType.OPEN_DOOR.value for wp in script.waypoints)


def test_list_parsing_with_spaces_and_quotes():
    content = 'label list_test\ncall talk_npc("list_words": [ '"'"'hi'"'"', "there" ])\n'
    # The above line mixes quoting styles; ensure parser tolerates it
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "list.in"
        p.write_text(content, encoding='utf-8')
        script = parse_in_to_script(p)
        assert any(wp.type == WaypointType.CALL_NPC.value for wp in script.waypoints)
        wp = next(wp for wp in script.waypoints if wp.type == WaypointType.CALL_NPC.value)
        assert isinstance(wp.options.get('list_words') or wp.options.get('list_words'), (list, type(None)))


def test_load_script_accepts_in_extension_via_load_script():
    content = 'node (10,11,7)\n'
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "t_in.in"
        p.write_text(content, encoding='utf-8')
        script = load_script(p)
        assert script.waypoints[0].x == 10
        assert script.waypoints[0].y == 11
