from __future__ import annotations

import json
from pathlib import Path

import pytest

from contracts.errors import PreflightFailed
from contracts.runtime import RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
from runtime.trade_basic_preflight import trade_basic_preflight
from runtime.trade_runner import execute_trade_tick


def _write_rois(tmp_path: Path) -> str:
    cfg = {
        'rois': {
            'trade_npc': {'x': 0, 'y': 0, 'width': 3, 'height': 1},
            'trade_inventory': {'x': 0, 'y': 1, 'width': 4, 'height': 1},
            'trade_action': {'x': 0, 'y': 2, 'width': 10, 'height': 10},
        }
    }
    p = tmp_path / 'rois.json'
    p.write_text(json.dumps(cfg), encoding='utf-8')
    return str(p)


def _make_ctx(tmp_path: Path, *, intent: str) -> RuntimeContext:
    cfg = RuntimeConfig(
        mode='mock',
        tick_hz=50.0,
        config_path=_write_rois(tmp_path),
        enable_cavebot=False,
        enable_targeting=False,
        enable_healing=False,
        enable_combat=False,
        trade_max_attempts=1,
        trade_max_ticks=1,
        trade_action=intent,  # type: ignore[arg-type]
        trade_expected_npc_id=1,
        trade_inventory_roi='trade_inventory',
        trade_npc_roi='trade_npc',
        trade_action_roi='trade_action',
    )
    return RuntimeContext(config=cfg, status=RuntimeStatus(state=RuntimeState.INIT), telemetry=RuntimeTelemetry())


def test_trade_basic_success_buy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('MOCK_TRADE_BUY_OK', '1')

    ctx = _make_ctx(tmp_path, intent='buy')
    cap, inp, binding = trade_basic_preflight(ctx)

    out = execute_trade_tick(ctx, capture=cap, input_=inp, binding=binding, tick_index=0, gate='trade_basic')

    assert out.success is True
    assert out.abort_reason is None
    assert int(ctx.trade.inputs_sent) == 1


def test_trade_basic_aborts_on_no_delta(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('MOCK_TRADE_NO_DELTA', '1')

    ctx = _make_ctx(tmp_path, intent='buy')
    cap, inp, binding = trade_basic_preflight(ctx)

    out = execute_trade_tick(ctx, capture=cap, input_=inp, binding=binding, tick_index=0, gate='trade_basic')

    assert out.success is False
    assert out.abort_reason == 'trade_no_inventory_delta'
    assert int(ctx.trade.inputs_sent) == 1


def test_trade_basic_preflight_rejects_wrong_npc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('MOCK_TRADE_WRONG_NPC', '1')

    ctx = _make_ctx(tmp_path, intent='buy')

    with pytest.raises(PreflightFailed) as e:
        trade_basic_preflight(ctx)

    assert str(e.value) == 'trade_wrong_npc'
