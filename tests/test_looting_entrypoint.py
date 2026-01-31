from __future__ import annotations

import json
from pathlib import Path

from _pytest.monkeypatch import MonkeyPatch

from looting_entrypoint import run_looting_only


def _write_rois(tmp_path: Path) -> str:
    cfg = {
        'rois': {
            # inventory_text encodes 3x uint16 in first 6 bytes => needs 2px*1px*3ch = 6 bytes
            'inventory_text': {'x': 2, 'y': 2, 'width': 2, 'height': 1},
            # free-mode rois
            'loot_container_open': {'x': 2, 'y': 6, 'width': 1, 'height': 1},
            'loot_corpse': {'x': 2, 'y': 10, 'width': 10, 'height': 10},
            'loot_take': {'x': 20, 'y': 10, 'width': 10, 'height': 10},
        }
    }
    p = tmp_path / 'rois.json'
    p.write_text(json.dumps(cfg), encoding='utf-8')
    return str(p)


def _base_env(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)

    monkeypatch.setenv('FRBOT_MODE', 'looting')
    monkeypatch.setenv('FRBOT_LOOTING_BACKEND', 'mock')
    monkeypatch.setenv('FRBOT_CONFIG_PATH', _write_rois(tmp_path))

    monkeypatch.setenv('FRBOT_TICK_HZ', '100')
    monkeypatch.setenv('FRBOT_LOOTING_MAX_TICKS', '5')
    monkeypatch.setenv('FRBOT_LOOTING_MAX_ATTEMPTS', '3')

    monkeypatch.setenv('FRBOT_QUICK_LOOT_KEY', 'R')

    monkeypatch.setenv('FRBOT_MOCK_CAPTURE_OK', '1')
    monkeypatch.setenv('FRBOT_MOCK_INPUT_OK', '1')
    monkeypatch.setenv('FRBOT_MOCK_WINDOW_OK', '1')
    monkeypatch.setenv('FRBOT_MOCK_WINDOW_FOREGROUND', '1')
    monkeypatch.setenv('FRBOT_MOCK_WINDOW_RECT_OK', '1')

    # start inventory snapshot
    monkeypatch.setenv('FRBOT_MOCK_INV_GOLD', '0')
    monkeypatch.setenv('FRBOT_MOCK_INV_CAP_USED', '0')

    monkeypatch.setenv('MOCK_LOOT_INVENTORY_DELTA', 'false')
    monkeypatch.setenv('MOCK_LOOT_CONTAINER_OPENS', 'false')
    monkeypatch.setenv('MOCK_LOOT_INVENTORY_READ_FAIL', 'false')
    monkeypatch.setenv('MOCK_LOOT_PREMIUM', 'true')


def test_looting_premium_success_inventory_delta(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _base_env(monkeypatch, tmp_path)

    monkeypatch.setenv('FRBOT_LOOTING_MODE', 'premium')
    monkeypatch.setenv('MOCK_LOOT_INVENTORY_DELTA', 'true')

    assert run_looting_only() == 0

    runtime_log = (tmp_path / 'diagnostics' / 'runtime.log').read_text(encoding='utf-8', errors='replace')
    assert '"status":"ok_looted"' in runtime_log
    # Exactly one tick on success.
    assert '"tick_index":1' not in runtime_log


def test_looting_premium_abort_no_delta_no_spam(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _base_env(monkeypatch, tmp_path)

    monkeypatch.setenv('FRBOT_LOOTING_MODE', 'premium')
    monkeypatch.setenv('FRBOT_LOOTING_MAX_ATTEMPTS', '1')

    assert run_looting_only() == 1

    fatal = (tmp_path / 'diagnostics' / 'fatal.log').read_text(encoding='utf-8', errors='replace')
    assert 'looting_unverified_loot' in fatal
    assert 'last_inventory' in fatal

    runtime_log = (tmp_path / 'diagnostics' / 'runtime.log').read_text(encoding='utf-8', errors='replace')
    # No spam: abort after first failed evidence.
    assert '"tick_index":1' not in runtime_log


def test_looting_preflight_inventory_unreadable_no_runtime_log(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _base_env(monkeypatch, tmp_path)

    monkeypatch.setenv('MOCK_LOOT_INVENTORY_READ_FAIL', 'true')

    assert run_looting_only() == 1

    fatal = (tmp_path / 'diagnostics' / 'fatal.log').read_text(encoding='utf-8', errors='replace')
    assert 'looting_inventory_unreadable' in fatal

    assert not (tmp_path / 'diagnostics' / 'runtime.log').exists()


def test_looting_free_success_open_then_take(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _base_env(monkeypatch, tmp_path)

    monkeypatch.setenv('FRBOT_LOOTING_MODE', 'free')
    monkeypatch.setenv('MOCK_LOOT_CONTAINER_OPENS', 'true')
    monkeypatch.setenv('MOCK_LOOT_INVENTORY_DELTA', 'true')

    assert run_looting_only() == 0

    runtime_log = (tmp_path / 'diagnostics' / 'runtime.log').read_text(encoding='utf-8', errors='replace')
    # Two ticks: open container then take.
    assert '"tick_index":0' in runtime_log
    assert '"tick_index":1' in runtime_log
    assert '"tick_index":2' not in runtime_log
    assert '"status":"ok_container_open"' in runtime_log
    assert '"status":"ok_looted"' in runtime_log


def test_looting_free_abort_container_never_opens(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _base_env(monkeypatch, tmp_path)

    monkeypatch.setenv('FRBOT_LOOTING_MODE', 'free')
    monkeypatch.setenv('FRBOT_LOOTING_MAX_ATTEMPTS', '1')
    monkeypatch.setenv('MOCK_LOOT_CONTAINER_OPENS', 'false')

    assert run_looting_only() == 1

    fatal = (tmp_path / 'diagnostics' / 'fatal.log').read_text(encoding='utf-8', errors='replace')
    assert 'looting_container_not_open' in fatal

    runtime_log = (tmp_path / 'diagnostics' / 'runtime.log').read_text(encoding='utf-8', errors='replace')
    assert '"tick_index":1' not in runtime_log
