from __future__ import annotations

import json
from pathlib import Path

import pytest

from contracts.errors import PreflightFailed
from contracts.runtime import RuntimeConfig, RuntimeContext, RuntimeState, RuntimeStatus, RuntimeTelemetry
from runtime.looting_full_preflight import looting_full_preflight
from runtime.looting_full_runner import execute_looting_full


def _write_rois(tmp_path: Path) -> str:
    cfg = {
        'rois': {
            # MockWorld encodes inventory_text via _roi_bytes_view(), which only supports height==1.
            # read_inventory_binary requires at least 6 bytes => width*3*height >= 6 => width >= 2.
            'inventory_text': {'x': 0, 'y': 0, 'width': 2, 'height': 1},
            'chat_loot_area': {'x': 0, 'y': 1, 'width': 60, 'height': 10},
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
        inventory_text_roi='inventory_text',
        quick_loot_key='R',
        looting_max_attempts_per_corpse=1,
        looting_max_ticks=1,
        looting_require_inventory_delta=True,
        looting_mode='premium',
        minimap_roi='minimap',
        window_hwnd=0,
        window_title_substring='',
    )
    return RuntimeContext(config=cfg, status=RuntimeStatus(state=RuntimeState.INIT), telemetry=RuntimeTelemetry())


def test_looting_full_loops_until_no_delta_after_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('MOCK_LOOT_INVENTORY_DELTA', '0')
    monkeypatch.setenv('MOCK_LOOT_INVENTORY_DELTA_COUNT', '2')

    ctx = _make_ctx(tmp_path)
    cap, inp, binding = looting_full_preflight(ctx)

    out = execute_looting_full(
        ctx,
        capture=cap,
        input_=inp,
        binding=binding,
        max_actions=10,
        stop_no_delta=1,
        gate='looting_full',
    )

    assert out.ok is True
    assert int(out.successes) == 2
    assert int(out.actions_sent) == 3
    assert str(out.stop_reason) == 'no_delta'
    assert int(ctx.looting.attempts_used) == 3


def test_looting_full_aborts_if_never_observes_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('MOCK_LOOT_INVENTORY_DELTA', '0')
    monkeypatch.setenv('MOCK_LOOT_INVENTORY_DELTA_COUNT', '0')

    ctx = _make_ctx(tmp_path)
    cap, inp, binding = looting_full_preflight(ctx)

    with pytest.raises(PreflightFailed) as e:
        execute_looting_full(
            ctx,
            capture=cap,
            input_=inp,
            binding=binding,
            max_actions=5,
            stop_no_delta=1,
            gate='looting_full',
        )

    assert str(e.value) == 'looting_full_no_evidence'
    assert int(ctx.looting.attempts_used) == 1
