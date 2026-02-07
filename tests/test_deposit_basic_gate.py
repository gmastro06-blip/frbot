from __future__ import annotations

import json
from pathlib import Path

import pytest

from contracts.errors import PreflightFailed
from contracts.runtime import RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
from runtime.deposit_basic_preflight import deposit_basic_preflight
from runtime.deposit_runner import execute_deposit_tick


def _write_rois(tmp_path: Path) -> str:
    cfg = {
        'rois': {
            # inventory_text is binary 0xBEEF (2x1 => 6 bytes)
            'inventory_text': {'x': 0, 'y': 0, 'width': 2, 'height': 1},
            # depot_container is 0xD00D encoding (min 6 bytes => 2x1 ok)
            'depot_container': {'x': 0, 'y': 1, 'width': 2, 'height': 1},
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
        enable_combat=False,
        deposit_max_attempts=1,
        deposit_max_ticks=1,
        deposit_key='D',
        inventory_text_roi='inventory_text',
        depot_container_roi='depot_container',
    )
    return RuntimeContext(config=cfg, status=RuntimeStatus(state=RuntimeState.INIT), telemetry=RuntimeTelemetry())


def test_deposit_basic_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('MOCK_DEPOSIT_SUCCESS', '1')

    ctx = _make_ctx(tmp_path)
    cap, inp, binding = deposit_basic_preflight(ctx)

    out = execute_deposit_tick(ctx, capture=cap, input_=inp, binding=binding, tick_index=0, gate='deposit_basic')

    assert out.success is True
    assert out.abort_reason is None
    assert int(ctx.deposit.inputs_sent) == 1
    assert int(ctx.deposit.attempts_used) == 1


def test_deposit_basic_aborts_on_partial_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('MOCK_DEPOSIT_PARTIAL', '1')

    ctx = _make_ctx(tmp_path)
    cap, inp, binding = deposit_basic_preflight(ctx)

    out = execute_deposit_tick(ctx, capture=cap, input_=inp, binding=binding, tick_index=0, gate='deposit_basic')

    assert out.success is False
    assert out.abort_reason == 'deposit_partial_failure'
    assert int(ctx.deposit.inputs_sent) == 1


def test_deposit_basic_preflight_rejects_closed_depot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('MOCK_DEPOSIT_DEPOT_CLOSED', '1')

    ctx = _make_ctx(tmp_path)

    with pytest.raises(PreflightFailed) as e:
        deposit_basic_preflight(ctx)

    assert str(e.value) == 'deposit_depot_not_open'
