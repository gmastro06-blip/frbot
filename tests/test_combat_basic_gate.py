from __future__ import annotations

import json
from pathlib import Path

import pytest

from contracts.errors import PreflightFailed
from contracts.runtime import RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
from runtime.combat_basic_preflight import combat_basic_preflight
from runtime.combat_basic_runner import execute_combat_basic_once


def _write_rois(tmp_path: Path) -> str:
    cfg = {
        'rois': {
            'battle_list': {'x': 2, 'y': 2, 'width': 80, 'height': 64},
            'world_view': {'x': 100, 'y': 200, 'width': 50, 'height': 50},
            'target_frame': {'x': 2, 'y': 70, 'width': 80, 'height': 20},
            'target_hp_bar': {'x': 2, 'y': 92, 'width': 80, 'height': 10},
            'combat_cooldown': {'x': 2, 'y': 106, 'width': 40, 'height': 10},
            'combat_feedback': {'x': 44, 'y': 106, 'width': 40, 'height': 10},
        }
    }
    p = tmp_path / 'rois.json'
    p.write_text(json.dumps(cfg), encoding='utf-8')
    return str(p)


def _make_ctx(tmp_path: Path) -> RuntimeContext:
    cfg = RuntimeConfig(
        mode='mock',
        tick_hz=50.0,
        config_path=_write_rois(tmp_path),
        enable_cavebot=False,
        enable_targeting=False,
        enable_healing=False,
        enable_combat=True,
        battle_list_roi='battle_list',
        target_frame_roi='target_frame',
        target_hp_bar_roi='target_hp_bar',
        combat_cooldown_roi='combat_cooldown',
        combat_feedback_roi='combat_feedback',
        attack_key='SPACE',
        combat_target_hp_decrease_min=0.02,
    )
    return RuntimeContext(
        config=cfg,
        status=RuntimeStatus(state=RuntimeState.INIT),
        telemetry=RuntimeTelemetry(),
    )


def test_combat_basic_success_target_hp_decrease(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('FRBOT_MOCK_BATTLE_LIST_ROWS', 'Orc:1:1')
    # Start unlocked.
    monkeypatch.setenv('FRBOT_MOCK_BATTLE_SELECTED_ROW', '')

    # Combat_basic now certifies by proving lock in AFTER.
    monkeypatch.setenv('FRBOT_COMBAT_BASIC_ACTION', 'battle_list_click')

    ctx = _make_ctx(tmp_path)
    cap, inp, binding = combat_basic_preflight(ctx)

    out = execute_combat_basic_once(ctx, capture=cap, input_=inp, binding=binding)

    assert out.ok is True
    assert out.evidence.evidence_ok is True
    assert out.evidence.evidence_kind == 'locked_after'
    assert bool(out.evidence.locked_after) is True
    assert int(ctx.combat.inputs_sent) == 1


def test_combat_basic_preflight_requires_hp_or_feedback_roi(tmp_path: Path) -> None:
    p = tmp_path / 'rois.json'
    p.write_text(
        json.dumps(
            {
                'rois': {
                    'battle_list': {'x': 2, 'y': 2, 'width': 80, 'height': 64},
                    'target_frame': {'x': 2, 'y': 70, 'width': 80, 'height': 20},
                    # No target_hp_bar and no combat_feedback.
                    'combat_cooldown': {'x': 2, 'y': 106, 'width': 40, 'height': 10},
                }
            }
        ),
        encoding='utf-8',
    )

    cfg = RuntimeConfig(
        mode='mock',
        tick_hz=50.0,
        config_path=str(p),
        enable_cavebot=False,
        enable_targeting=False,
        enable_healing=False,
        enable_combat=True,
        battle_list_roi='battle_list',
        target_frame_roi='target_frame',
        target_hp_bar_roi='target_hp_bar',
        combat_cooldown_roi='combat_cooldown',
        combat_feedback_roi='combat_feedback',
        attack_key='SPACE',
        combat_target_hp_decrease_min=0.02,
    )
    ctx = RuntimeContext(config=cfg, status=RuntimeStatus(state=RuntimeState.INIT), telemetry=RuntimeTelemetry())

    with pytest.raises(PreflightFailed) as e:
        combat_basic_preflight(ctx)
    assert str(e.value) == 'combat_invalid_state'


def test_combat_basic_runner_aborts_on_unverified_action(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('FRBOT_MOCK_BATTLE_LIST_ROWS', 'Orc:1:1')
    # Start unlocked and do an action that does not lock.
    monkeypatch.setenv('FRBOT_MOCK_BATTLE_SELECTED_ROW', '')
    monkeypatch.setenv('FRBOT_COMBAT_BASIC_ACTION', 'attack_key:SPACE')

    ctx = _make_ctx(tmp_path)
    cap, inp, binding = combat_basic_preflight(ctx)

    with pytest.raises(PreflightFailed) as e:
        execute_combat_basic_once(ctx, capture=cap, input_=inp, binding=binding)
    assert str(e.value) == 'combat_unverified_action'
    assert int(ctx.combat.inputs_sent) == 1


def test_combat_basic_success_feedback_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('FRBOT_MOCK_BATTLE_LIST_ROWS', 'Orc:1:1')
    monkeypatch.setenv('FRBOT_MOCK_BATTLE_SELECTED_ROW', '')
    monkeypatch.setenv('FRBOT_COMBAT_BASIC_ACTION', 'battle_list_click')

    ctx = _make_ctx(tmp_path)
    cap, inp, binding = combat_basic_preflight(ctx)

    out = execute_combat_basic_once(ctx, capture=cap, input_=inp, binding=binding)

    assert out.ok is True
    assert out.evidence.evidence_ok is True
    assert out.evidence.evidence_kind == 'locked_after'
    assert bool(out.evidence.locked_after) is True
    assert int(ctx.combat.inputs_sent) == 1


def test_combat_basic_click_action_routes_to_click(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('FRBOT_MOCK_BATTLE_LIST_ROWS', 'Orc:1:1')
    monkeypatch.setenv('FRBOT_MOCK_BATTLE_SELECTED_ROW', '')

    monkeypatch.setenv('FRBOT_COMBAT_BASIC_ACTION', 'battle_list_click')

    ctx = _make_ctx(tmp_path)
    cap, inp, binding = combat_basic_preflight(ctx)

    calls: list[tuple[str, tuple[int, int] | str]] = []

    orig_press_key = inp.press_key
    orig_click = inp.click

    def _press_key(key: str) -> None:
        calls.append(('press_key', str(key)))
        return orig_press_key(key)

    def _click(x: int, y: int) -> None:
        calls.append(('click', (int(x), int(y))))
        return orig_click(int(x), int(y))

    monkeypatch.setattr(inp, 'press_key', _press_key, raising=True)
    monkeypatch.setattr(inp, 'click', _click, raising=True)

    out = execute_combat_basic_once(ctx, capture=cap, input_=inp, binding=binding)
    assert out.ok is True
    assert int(ctx.combat.inputs_sent) == 1
    assert any(c[0] == 'click' for c in calls)
    assert not any(c[0] == 'press_key' for c in calls)


def test_combat_basic_click_rel_parses_floats(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('FRBOT_MOCK_BATTLE_LIST_ROWS', 'Orc:1:1')
    monkeypatch.setenv('FRBOT_MOCK_BATTLE_SELECTED_ROW', '')

    monkeypatch.setenv('FRBOT_COMBAT_BASIC_ACTION', 'battle_list_click')
    monkeypatch.setenv('FRBOT_COMBAT_BASIC_CLICK_REL', '0.50,0.50')

    ctx = _make_ctx(tmp_path)
    cap, inp, binding = combat_basic_preflight(ctx)

    clicked: list[tuple[int, int]] = []
    orig_click = inp.click

    def _click(x: int, y: int) -> None:
        clicked.append((int(x), int(y)))
        return orig_click(int(x), int(y))

    monkeypatch.setattr(inp, 'click', _click, raising=True)

    with pytest.raises(PreflightFailed) as e:
        execute_combat_basic_once(ctx, capture=cap, input_=inp, binding=binding)
    assert str(e.value) == 'combat_unverified_action'
    assert clicked, 'expected click to be called'
    # battle_list ROI: x=2,y=2,w=80,h=64 => center is (42,34)
    assert clicked[0] == (42, 34)


def test_combat_basic_click_roi_overrides_battle_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('FRBOT_MOCK_BATTLE_LIST_ROWS', 'Orc:1:1')
    monkeypatch.setenv('FRBOT_MOCK_BATTLE_SELECTED_ROW', '')

    monkeypatch.setenv('FRBOT_COMBAT_BASIC_ACTION', 'battle_list_click')
    monkeypatch.setenv('FRBOT_COMBAT_BASIC_CLICK_ROI', 'world_view')
    monkeypatch.setenv('FRBOT_COMBAT_BASIC_CLICK_REL', '0.50,0.50')

    ctx = _make_ctx(tmp_path)
    cap, inp, binding = combat_basic_preflight(ctx)

    clicked: list[tuple[int, int]] = []
    orig_click = inp.click

    def _click(x: int, y: int) -> None:
        clicked.append((int(x), int(y)))
        return orig_click(int(x), int(y))

    monkeypatch.setattr(inp, 'click', _click, raising=True)

    with pytest.raises(PreflightFailed) as e:
        execute_combat_basic_once(ctx, capture=cap, input_=inp, binding=binding)
    assert str(e.value) == 'combat_unverified_action'
    assert clicked, 'expected click to be called'
    # world_view ROI: x=100,y=200,w=50,h=50 => center is (125,225)
    assert clicked[0] == (125, 225)


def test_combat_basic_click_then_key_is_disallowed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('FRBOT_MOCK_BATTLE_LIST_ROWS', 'Orc:1:1')
    monkeypatch.setenv('FRBOT_MOCK_BATTLE_SELECTED_ROW', '')
    monkeypatch.setenv('FRBOT_COMBAT_BASIC_ACTION', 'click_then_key')

    ctx = _make_ctx(tmp_path)
    cap, inp, binding = combat_basic_preflight(ctx)

    with pytest.raises(PreflightFailed) as e:
        execute_combat_basic_once(ctx, capture=cap, input_=inp, binding=binding)
    assert str(e.value) == 'combat_invalid_state'
