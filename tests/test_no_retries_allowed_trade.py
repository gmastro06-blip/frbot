from __future__ import annotations

import json
from pathlib import Path

from _pytest.monkeypatch import MonkeyPatch

from trade_entrypoint import run_trade_only


def _write_rois(tmp_path: Path) -> str:
    cfg = {
        'rois': {
            'trade_npc': {'x': 2, 'y': 2, 'width': 2, 'height': 1},
            'trade_inventory': {'x': 2, 'y': 6, 'width': 3, 'height': 1},
            'trade_action': {'x': 2, 'y': 10, 'width': 1, 'height': 1},
        }
    }
    p = tmp_path / 'rois.json'
    p.write_text(json.dumps(cfg), encoding='utf-8')
    return str(p)


def test_trade_no_retries_even_if_max_attempts_high(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    monkeypatch.setenv('FRBOT_MODE', 'trade')
    monkeypatch.setenv('FRBOT_TRADE_BACKEND', 'mock')
    monkeypatch.setenv('FRBOT_CONFIG_PATH', _write_rois(tmp_path))

    monkeypatch.setenv('FRBOT_TICK_HZ', '100')

    monkeypatch.setenv('FRBOT_TRADE_ACTION', 'buy')
    monkeypatch.setenv('FRBOT_TRADE_MAX_TICKS', '5')
    monkeypatch.setenv('FRBOT_TRADE_MAX_ATTEMPTS', '20')
    monkeypatch.setenv('FRBOT_TRADE_EXPECTED_NPC_ID', '7')

    monkeypatch.setenv('FRBOT_TRADE_INVENTORY_ROI', 'trade_inventory')
    monkeypatch.setenv('FRBOT_TRADE_NPC_ROI', 'trade_npc')
    monkeypatch.setenv('FRBOT_TRADE_ACTION_ROI', 'trade_action')

    monkeypatch.setenv('FRBOT_MOCK_CAPTURE_OK', '1')
    monkeypatch.setenv('FRBOT_MOCK_INPUT_OK', '1')
    monkeypatch.setenv('FRBOT_MOCK_WINDOW_OK', '1')
    monkeypatch.setenv('FRBOT_MOCK_WINDOW_FOREGROUND', '1')
    monkeypatch.setenv('FRBOT_MOCK_WINDOW_RECT_OK', '1')

    monkeypatch.setenv('FRBOT_MOCK_TRADE_GOLD', '100')
    monkeypatch.setenv('FRBOT_MOCK_TRADE_ITEMS', '0')
    monkeypatch.setenv('FRBOT_MOCK_TRADE_CAP_USED', '10')

    monkeypatch.setenv('MOCK_TRADE_NPC_PRESENT', 'true')
    monkeypatch.setenv('MOCK_TRADE_WRONG_NPC', 'false')

    monkeypatch.setenv('MOCK_TRADE_BUY_OK', 'false')
    monkeypatch.setenv('MOCK_TRADE_SELL_OK', 'false')
    monkeypatch.setenv('MOCK_TRADE_NO_DELTA', 'true')
    monkeypatch.setenv('MOCK_TRADE_GOLD_ONLY', 'false')
    monkeypatch.setenv('MOCK_TRADE_ITEM_ONLY', 'false')

    assert run_trade_only() == 1

    runtime_path = tmp_path / 'diagnostics' / 'runtime.log'
    if not runtime_path.exists():
        fatal_path = tmp_path / 'diagnostics' / 'fatal.log'
        fatal = fatal_path.read_text(encoding='utf-8', errors='replace') if fatal_path.exists() else '(fatal.log missing)'
        raise AssertionError(f"Expected runtime.log to exist (preflight should pass). fatal.log:\n{fatal}")

    runtime_log = runtime_path.read_text(encoding='utf-8', errors='replace')
    assert '"inputs_sent":1' in runtime_log
    assert '"inputs_sent":2' not in runtime_log
