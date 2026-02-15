from __future__ import annotations

from pathlib import Path

from tools.import_waypoints_in import import_waypoints_in


def test_import_waypoints_in_maps_supported_and_warns_unknown(tmp_path: Path) -> None:
    src = tmp_path / "route.in"
    src.write_text(
        "\n".join(
            [
                "label start",
                "node (100,200,7)",
                "stand (101,200,7)",
                "ladder (102,200,7)",
                "rope (103,200,7)",
                "shovel (104,200,7)",
                "pick (105,200,7)",
                "action travel_carlin",
                "action wait",
                "call talk_npc(\"list_words\":[\"hi\"])",
                "action custom_unknown",
                "call totally_unknown(\"x\":1)",
            ]
        ),
        encoding="utf-8",
    )

    result = import_waypoints_in(input_path=src, script_name="legacy_import")

    script = result.script
    assert script.name == "legacy_import"
    assert len(script.waypoints) == 11

    wp_types = [wp.type for wp in script.waypoints]
    assert wp_types == [
        "walk",
        "walk",
        "use_ladder",
        "rope",
        "use_right_click",
        "use_right_click",
        "travel",
        "walk_ignore",
        "walk_ignore",
        "walk_ignore",
        "walk_ignore",
    ]

    assert script.waypoints[4].options.get("action_kind") == "shovel"
    assert script.waypoints[5].options.get("action_kind") == "pick"
    assert script.waypoints[6].options.get("action_kind") == "travel_carlin"
    assert script.waypoints[7].options.get("action_kind") == "wait"
    assert script.waypoints[8].options.get("legacy_call") == "talk_npc"
    assert script.waypoints[9].options.get("action_kind") == "custom_unknown"
    assert script.waypoints[10].options.get("legacy_call") == "totally_unknown"

    import_meta = (script.metadata or {}).get("import", {})
    assert isinstance(import_meta, dict)
    assert import_meta.get("format") == "legacy_waypoints_in"
    assert (import_meta.get("labels") or {}).get("start") == 0

    warnings = import_meta.get("warnings") or []
    assert warnings == []


def test_import_waypoints_in_plus_maps_walk_door_and_selected_actions(tmp_path: Path) -> None:
    src = tmp_path / "route_plus.in"
    src.write_text(
        "\n".join(
            [
                "walk (200,300,7)",
                "door (201,300,7)",
                "action check_time",
                "action end",
            ]
        ),
        encoding="utf-8",
    )

    result = import_waypoints_in(input_path=src, script_name="legacy_plus")
    script = result.script

    assert len(script.waypoints) == 4
    assert [wp.type for wp in script.waypoints] == [
        "walk",
        "open_door",
        "walk_ignore",
        "walk_ignore",
    ]

    assert script.waypoints[1].options.get("action_kind") == "door"
    assert script.waypoints[2].options.get("action_kind") == "check_time"
    assert script.waypoints[3].options.get("action_kind") == "end"


def test_import_waypoints_in_maps_load_and_conditional_calls(tmp_path: Path) -> None:
    src = tmp_path / "route_calls.in"
    src.write_text(
        "\n".join(
            [
                "node (10,20,7)",
                "load scripts/general/refill_port_hope.in",
                "call conditional_jump_script_options(\"var_name\":\"task\")",
                "call check_kill_count(\"monster_name\":\"tarantula\")",
                "call say(\"sentence\":\"hi\")",
            ]
        ),
        encoding="utf-8",
    )

    result = import_waypoints_in(input_path=src, script_name="legacy_calls")
    script = result.script

    assert len(script.waypoints) == 5
    assert [wp.type for wp in script.waypoints] == [
        "walk",
        "walk_ignore",
        "walk_ignore",
        "walk_ignore",
        "walk_ignore",
    ]

    assert script.waypoints[1].options.get("action_kind") == "load"
    assert script.waypoints[2].options.get("legacy_call") == "conditional_jump_script_options"
    assert script.waypoints[3].options.get("legacy_call") == "check_kill_count"
    assert script.waypoints[4].options.get("legacy_call") == "say"


def test_import_waypoints_in_maps_legacy_quest_actions(tmp_path: Path) -> None:
    src = tmp_path / "route_quest_actions.in"
    src.write_text(
        "\n".join(
            [
                "node (10,10,7)",
                "action buy_ticket",
                "action levitate_north_up",
                "action angus",
                "action karith",
            ]
        ),
        encoding="utf-8",
    )

    result = import_waypoints_in(input_path=src, script_name="legacy_quest_actions")
    script = result.script

    assert len(script.waypoints) == 5
    assert [wp.type for wp in script.waypoints] == [
        "walk",
        "walk_ignore",
        "walk_ignore",
        "walk_ignore",
        "walk_ignore",
    ]

    assert script.waypoints[1].options.get("action_kind") == "buy_ticket"
    assert script.waypoints[2].options.get("action_kind") == "levitate_north_up"
    assert script.waypoints[3].options.get("action_kind") == "angus"
    assert script.waypoints[4].options.get("action_kind") == "karith"


def test_import_waypoints_in_maps_use_command_and_no_anchor_warning(tmp_path: Path) -> None:
    src = tmp_path / "route_use.in"
    src.write_text(
        "\n".join(
            [
                "load scripts/general/refill_port_hope.in",
                "use (32954, 32695, 8)",
                "node (100,100,7)",
            ]
        ),
        encoding="utf-8",
    )

    result = import_waypoints_in(input_path=src, script_name="legacy_use")
    script = result.script

    assert len(script.waypoints) == 3
    assert [wp.type for wp in script.waypoints] == [
        "use_right_click",
        "walk",
        "walk_ignore",
    ]
    assert script.waypoints[0].options.get("action_kind") == "use"
    assert script.waypoints[2].options.get("action_kind") == "load"
    assert ((script.metadata or {}).get("import", {}).get("warnings") or []) == []
