from __future__ import annotations

import json
from pathlib import Path

import pytest

from contracts.errors import PreflightFailed
from contracts.runtime import RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
from runtime.targeting_preflight import targeting_preflight
from runtime.targeting_runner import targeting_tick


def _write_rois(tmp_path: Path) -> str:
    cfg = {
        'rois': {
            'battle_list': {'x': 2, 'y': 2, 'width': 80, 'height': 64},
            'target_frame': {'x': 2, 'y': 70, 'width': 80, 'height': 20},
        }
    }
    p = tmp_path / 'rois.json'
    p.write_text(json.dumps(cfg), encoding='utf-8')
    return str(p)


def _make_ctx(tmp_path: Path, *, enable_targeting: bool = True) -> RuntimeContext:
    cfg = RuntimeConfig(
        mode='mock',
        tick_hz=50.0,
        config_path=_write_rois(tmp_path),
        enable_cavebot=False,
        enable_targeting=bool(enable_targeting),
        battle_list_roi='battle_list',
        target_frame_roi='target_frame',
        max_attempts_per_target=2,
        max_time_ms_per_target=5_000,
    )
    return RuntimeContext(
        config=cfg,
        status=RuntimeStatus(state=RuntimeState.INIT),
        telemetry=RuntimeTelemetry(),
    )


def test_battle_list_detection_abort(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('FRBOT_MOCK_BATTLE_LIST_ROWS', '')

    ctx = _make_ctx(tmp_path)
    with pytest.raises(PreflightFailed) as e:
        targeting_preflight(ctx)
    assert str(e.value) == 'battle_list_not_detected'


def test_target_selection_single_candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('FRBOT_MOCK_BATTLE_LIST_ROWS', 'Rat:1:1')
    monkeypatch.setenv('FRBOT_MOCK_BATTLE_CLICK_BEHAVIOR', 'normal')

    ctx = _make_ctx(tmp_path)
    capture, input_, binding = targeting_preflight(ctx)

    targeting_tick(ctx, capture, input_, binding)

    assert ctx.targeting.target.locked is True
    assert ctx.targeting.target.target_name == 'Rat'


def test_target_selection_multiple_candidates_deterministic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('FRBOT_MOCK_BATTLE_LIST_ROWS', 'Orc:1:1;Rat:1:1')
    monkeypatch.setenv('FRBOT_MOCK_BATTLE_CLICK_BEHAVIOR', 'normal')

    ctx = _make_ctx(tmp_path)
    capture, input_, binding = targeting_preflight(ctx)

    targeting_tick(ctx, capture, input_, binding)

    assert ctx.targeting.target.locked is True
    assert ctx.targeting.target.target_name == 'Orc'


def test_target_click_without_highlight_aborts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('FRBOT_MOCK_BATTLE_LIST_ROWS', 'Rat:1:1')
    monkeypatch.setenv('FRBOT_MOCK_BATTLE_CLICK_BEHAVIOR', 'no_highlight')

    ctx = _make_ctx(tmp_path)
    capture, input_, binding = targeting_preflight(ctx)

    targeting_tick(ctx, capture, input_, binding)
    with pytest.raises(PreflightFailed) as e:
        targeting_tick(ctx, capture, input_, binding)
    assert str(e.value) == 'targeting_unstable_or_ambiguous'


def test_target_wrong_row_highlight_aborts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('FRBOT_MOCK_BATTLE_LIST_ROWS', 'Orc:1:1;Rat:1:1')
    monkeypatch.setenv('FRBOT_MOCK_BATTLE_CLICK_BEHAVIOR', 'wrong_row')

    ctx = _make_ctx(tmp_path)
    capture, input_, binding = targeting_preflight(ctx)

    targeting_tick(ctx, capture, input_, binding)
    with pytest.raises(PreflightFailed) as e:
        targeting_tick(ctx, capture, input_, binding)
    assert str(e.value) == 'targeting_unstable_or_ambiguous'


def test_targeting_never_loops(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('FRBOT_MOCK_BATTLE_LIST_ROWS', 'Orc:1:1;Rat:1:1')
    monkeypatch.setenv('FRBOT_MOCK_BATTLE_CLICK_BEHAVIOR', 'no_highlight')

    ctx = _make_ctx(tmp_path)
    capture, input_, binding = targeting_preflight(ctx)

    # Tick twice to exhaust attempts. Must abort on the same chosen target.
    targeting_tick(ctx, capture, input_, binding)
    assert ctx.targeting.attempt_target_name == 'Orc'

    with pytest.raises(PreflightFailed) as e:
        targeting_tick(ctx, capture, input_, binding)
    assert str(e.value) == 'targeting_unstable_or_ambiguous'
    assert ctx.targeting.attempt_target_name == 'Orc'
