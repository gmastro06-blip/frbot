from __future__ import annotations

import json
from pathlib import Path

from _pytest.monkeypatch import MonkeyPatch

from deposit_entrypoint import run_deposit_only


def _write_rois(tmp_path: Path) -> str:
    cfg = {
        'rois': {
            # inventory_text encodes 3x uint16 in first 6 bytes => needs 2px*1px*3ch = 6 bytes
            'inventory_text': {'x': 2, 'y': 2, 'width': 2, 'height': 1},
            # depot_container encodes 3x uint16 in first 6 bytes
            'depot_container': {'x': 2, 'y': 6, 'width': 2, 'height': 1},
        }
    }
    p = tmp_path / 'rois.json'
    p.write_text(json.dumps(cfg), encoding='utf-8')
    return str(p)


def _base_env(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)

    monkeypatch.setenv('FRBOT_MODE', 'deposit')
    monkeypatch.setenv('FRBOT_DEPOSIT_BACKEND', 'mock')
    monkeypatch.setenv('FRBOT_CONFIG_PATH', _write_rois(tmp_path))

    monkeypatch.setenv('FRBOT_TICK_HZ', '100')

    monkeypatch.setenv('FRBOT_DEPOSIT_KEY', 'D')
    monkeypatch.setenv('FRBOT_DEPOSIT_MAX_TICKS', '5')
    monkeypatch.setenv('FRBOT_DEPOSIT_MAX_ATTEMPTS', '3')

    monkeypatch.setenv('FRBOT_INVENTORY_TEXT_ROI', 'inventory_text')
    monkeypatch.setenv('FRBOT_DEPOT_CONTAINER_ROI', 'depot_container')

    monkeypatch.setenv('FRBOT_MOCK_CAPTURE_OK', '1')
    monkeypatch.setenv('FRBOT_MOCK_INPUT_OK', '1')
    monkeypatch.setenv('FRBOT_MOCK_WINDOW_OK', '1')
    monkeypatch.setenv('FRBOT_MOCK_WINDOW_FOREGROUND', '1')
    monkeypatch.setenv('FRBOT_MOCK_WINDOW_RECT_OK', '1')

    monkeypatch.setenv('FRBOT_MOCK_INV_GOLD', '5')
    monkeypatch.setenv('FRBOT_MOCK_INV_CAP_USED', '5')
    monkeypatch.setenv('FRBOT_MOCK_DEPOT_COUNT', '0')

    monkeypatch.setenv('MOCK_DEPOSIT_SUCCESS', 'false')
    monkeypatch.setenv('MOCK_DEPOSIT_NO_DELTA', 'false')
    monkeypatch.setenv('MOCK_DEPOSIT_PARTIAL', 'false')
    monkeypatch.setenv('MOCK_DEPOSIT_DEPOT_CLOSED', 'false')
    monkeypatch.setenv('MOCK_DEPOSIT_INVENTORY_UNREADABLE', 'false')


def test_deposit_success_inventory_delta(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _base_env(monkeypatch, tmp_path)

    monkeypatch.setenv('MOCK_DEPOSIT_SUCCESS', 'true')

    assert run_deposit_only() == 0

    runtime_log = (tmp_path / 'diagnostics' / 'runtime.log').read_text(encoding='utf-8', errors='replace')
    assert '"success":true' in runtime_log
    assert '"abort_reason":null' in runtime_log
    # Exactly one input on success.
    assert '"inputs_sent":1' in runtime_log
    assert '"inputs_sent":2' not in runtime_log


def test_deposit_abort_no_delta(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _base_env(monkeypatch, tmp_path)

    monkeypatch.setenv('FRBOT_DEPOSIT_MAX_ATTEMPTS', '1')
    monkeypatch.setenv('MOCK_DEPOSIT_NO_DELTA', 'true')

    assert run_deposit_only() == 1

    fatal = (tmp_path / 'diagnostics' / 'fatal.log').read_text(encoding='utf-8', errors='replace')
    assert 'deposit_no_inventory_delta' in fatal
    assert 'inventory_before=' in fatal
    assert 'inventory_after=' in fatal


def test_deposit_abort_partial(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _base_env(monkeypatch, tmp_path)

    monkeypatch.setenv('MOCK_DEPOSIT_PARTIAL', 'true')

    assert run_deposit_only() == 1

    fatal = (tmp_path / 'diagnostics' / 'fatal.log').read_text(encoding='utf-8', errors='replace')
    assert 'deposit_partial_failure' in fatal


def test_deposit_abort_depot_closed(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _base_env(monkeypatch, tmp_path)

    monkeypatch.setenv('MOCK_DEPOSIT_DEPOT_CLOSED', 'true')

    assert run_deposit_only() == 1

    fatal = (tmp_path / 'diagnostics' / 'fatal.log').read_text(encoding='utf-8', errors='replace')
    assert 'deposit_depot_not_open' in fatal

    # Preflight failed => runtime.log must not exist.
    assert not (tmp_path / 'diagnostics' / 'runtime.log').exists()


def test_deposit_abort_inventory_unreadable(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _base_env(monkeypatch, tmp_path)

    monkeypatch.setenv('MOCK_DEPOSIT_INVENTORY_UNREADABLE', 'true')

    assert run_deposit_only() == 1

    fatal = (tmp_path / 'diagnostics' / 'fatal.log').read_text(encoding='utf-8', errors='replace')
    assert 'deposit_inventory_unreadable' in fatal

    # Preflight failed => runtime.log must not exist.
    assert not (tmp_path / 'diagnostics' / 'runtime.log').exists()


def test_deposit_no_spam_inputs(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _base_env(monkeypatch, tmp_path)

    monkeypatch.setenv('FRBOT_DEPOSIT_MAX_ATTEMPTS', '1')
    monkeypatch.setenv('MOCK_DEPOSIT_NO_DELTA', 'true')

    assert run_deposit_only() == 1

    runtime_log = (tmp_path / 'diagnostics' / 'runtime.log').read_text(encoding='utf-8', errors='replace')
    assert '"inputs_sent":1' in runtime_log
    assert '"inputs_sent":2' not in runtime_log


def test_deposit_window_binding_lost(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _base_env(monkeypatch, tmp_path)

    monkeypatch.setenv('FRBOT_MOCK_WINDOW_FOREGROUND', '0')

    assert run_deposit_only() == 1

    fatal = (tmp_path / 'diagnostics' / 'fatal.log').read_text(encoding='utf-8', errors='replace')
    assert 'deposit_window_binding_lost' in fatal

    # Preflight failed => runtime.log must not exist.
    assert not (tmp_path / 'diagnostics' / 'runtime.log').exists()
