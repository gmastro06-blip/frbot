import tempfile
from pathlib import Path

from storage import parse_in_to_script, load_script
from models import WaypointType


def test_parse_single_quotes_and_unquoted_keys():
    content = '''
label test1
node (100, 200, 7)
call talk_npc('list_words':['hello','yes'])
'''
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "t1.in"
        p.write_text(content, encoding='utf-8')
        script = parse_in_to_script(p)
        assert script.name == 'test1'
        assert any(wp.type == WaypointType.CALL_NPC.value for wp in script.waypoints)


def test_parse_block_and_line_comments_and_hash_inline():
    content = '''
/* block comment should be ignored */
label commented
node (1,2,7) // trailing comment
# full-line hash comment
node (3,4,7) # inline hash comment
call talk_npc("list_words":["a","b"]) /* another block */
'''
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "t2.in"
        p.write_text(content, encoding='utf-8')
        script = parse_in_to_script(p)
        # should have three waypoints: node, node, call
        assert len(script.waypoints) == 3
        assert script.waypoints[2].type == WaypointType.CALL_NPC.value


def test_parse_action_with_kv_args_and_lists():
    content = '''
label travel_test
action travel_carlin("foo":"bar", "count":"3")
call conditional_jump_script_options("var_name":"enhanced", "label_jump":"enhanced", "label_skip":"go_train_edron")
'''
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "t3.in"
        p.write_text(content, encoding='utf-8')
        script = parse_in_to_script(p)
        assert any(wp.type == 'conditional_jump' for wp in script.waypoints)
        # action travel should produce a travel-type waypoint (type may be 'travel' or include action)
        assert any('travel' in str(wp.options.get('action', '')) for wp in script.waypoints)


def test_load_script_handles_in_ext_via_load_script():
    content = 'node (10,11,7)\n'
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "t4.in"
        p.write_text(content, encoding='utf-8')
        script = load_script(p)
        assert script.waypoints[0].x == 10
        assert script.waypoints[0].y == 11
