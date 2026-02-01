from __future__ import annotations

import json
from pathlib import Path

from _pytest.monkeypatch import MonkeyPatch

from trade_entrypoint import run_trade_only


def _write_rois(tmp_path: Path) -> str:
    cfg = {
        'rois': {
            # trade_npc encodes 3x uint16 in first 6 bytes => 2px*1px*3ch = 6 bytes
            'trade_npc': {'x': 2, 'y': 2, 'width': 2, 'height': 1},
            # trade_inventory encodes 4x uint16 in first 8 bytes => 3px*1px*3ch = 9 bytes
            'trade_inventory': {'x': 2, 'y': 6, 'width': 3, 'height': 1},
            # trade_action is clicked (center point)
            'trade_action': {'x': 2, 'y': 10, 'width': 1, 'height': 1},
        }
    }
    p = tmp_path / 'rois.json'
    p.write_text(json.dumps(cfg), encoding='utf-8')
    return str(p)


def _base_env(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)

    monkeypatch.setenv('FRBOT_MODE', 'trade')
    monkeypatch.setenv('FRBOT_TRADE_BACKEND', 'mock')
    monkeypatch.setenv('FRBOT_CONFIG_PATH', _write_rois(tmp_path))

    monkeypatch.setenv('FRBOT_TICK_HZ', '100')

    monkeypatch.setenv('FRBOT_TRADE_ACTION', 'buy')
    monkeypatch.setenv('FRBOT_TRADE_MAX_TICKS', '5')
    monkeypatch.setenv('FRBOT_TRADE_MAX_ATTEMPTS', '3')
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
    monkeypatch.setenv('MOCK_TRADE_NO_DELTA', 'false')
    monkeypatch.setenv('MOCK_TRADE_GOLD_ONLY', 'false')
    monkeypatch.setenv('MOCK_TRADE_ITEM_ONLY', 'false')


def test_trade_success_buy_delta(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _base_env(monkeypatch, tmp_path)

    monkeypatch.setenv('FRBOT_TRADE_ACTION', 'buy')
    monkeypatch.setenv('MOCK_TRADE_BUY_OK', 'true')

    assert run_trade_only() == 0

    runtime_log = (tmp_path / 'diagnostics' / 'runtime.log').read_text(encoding='utf-8', errors='replace')
    assert '"intent_type":"buy"' in runtime_log
    assert '"gold_before":100' in runtime_log
    assert '"gold_after":99' in runtime_log
    assert '"items_before":0' in runtime_log
    assert '"items_after":1' in runtime_log
    assert '"inputs_sent":1' in runtime_log
    assert '"inputs_sent":2' not in runtime_log
    assert '"status":"SUCCESS"' in runtime_log


def test_trade_abort_no_delta(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _base_env(monkeypatch, tmp_path)

    monkeypatch.setenv('FRBOT_TRADE_MAX_ATTEMPTS', '1')
    monkeypatch.setenv('MOCK_TRADE_NO_DELTA', 'true')

    assert run_trade_only() == 1

    fatal = (tmp_path / 'diagnostics' / 'fatal.log').read_text(encoding='utf-8', errors='replace')
    assert 'trade_no_inventory_delta' in fatal
    assert 'npc_identity=' in fatal
    assert 'last_inventory_snapshot=' in fatal


def test_trade_no_spam_inputs(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _base_env(monkeypatch, tmp_path)

    monkeypatch.setenv('FRBOT_TRADE_MAX_ATTEMPTS', '1')
    monkeypatch.setenv('FRBOT_TRADE_ACTION', 'buy')
    monkeypatch.setenv('MOCK_TRADE_GOLD_ONLY', 'true')

    assert run_trade_only() == 1

    runtime_log = (tmp_path / 'diagnostics' / 'runtime.log').read_text(encoding='utf-8', errors='replace')
    assert '"inputs_sent":1' in runtime_log
    assert '"inputs_sent":2' not in runtime_log

    fatal = (tmp_path / 'diagnostics' / 'fatal.log').read_text(encoding='utf-8', errors='replace')
    assert 'trade_unverified_action' in fatal


def test_trade_abort_npc_not_detected_preflight(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _base_env(monkeypatch, tmp_path)

    monkeypatch.setenv('MOCK_TRADE_NPC_PRESENT', 'false')

    assert run_trade_only() == 1

    fatal = (tmp_path / 'diagnostics' / 'fatal.log').read_text(encoding='utf-8', errors='replace')
    assert 'trade_npc_not_detected' in fatal

    # Preflight failed => runtime.log must not exist.
    assert not (tmp_path / 'diagnostics' / 'runtime.log').exists()


def test_trade_abort_wrong_npc_preflight(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _base_env(monkeypatch, tmp_path)

    monkeypatch.setenv('MOCK_TRADE_WRONG_NPC', 'true')

    assert run_trade_only() == 1

    fatal = (tmp_path / 'diagnostics' / 'fatal.log').read_text(encoding='utf-8', errors='replace')
    assert 'trade_wrong_npc' in fatal

    # Preflight failed => runtime.log must not exist.
    assert not (tmp_path / 'diagnostics' / 'runtime.log').exists()


def test_trade_abort_unverified_action_missing_roi(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _base_env(monkeypatch, tmp_path)

    monkeypatch.setenv('FRBOT_TRADE_ACTION_ROI', 'missing_roi')

    assert run_trade_only() == 1

    fatal = (tmp_path / 'diagnostics' / 'fatal.log').read_text(encoding='utf-8', errors='replace')
    assert 'trade_unverified_action' in fatal

    # Preflight failed => runtime.log must not exist.
    assert not (tmp_path / 'diagnostics' / 'runtime.log').exists()


def test_trade_window_binding_lost_preflight(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _base_env(monkeypatch, tmp_path)

    monkeypatch.setenv('FRBOT_MOCK_WINDOW_FOREGROUND', '0')

    assert run_trade_only() == 1

    fatal = (tmp_path / 'diagnostics' / 'fatal.log').read_text(encoding='utf-8', errors='replace')
    assert 'trade_window_binding_lost' in fatal

    # Preflight failed => runtime.log must not exist.
    assert not (tmp_path / 'diagnostics' / 'runtime.log').exists()
