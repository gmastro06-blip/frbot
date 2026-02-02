from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

from cavebot_entrypoint import run_cavebot_only
from combat_entrypoint import run_combat_only
from deposit_entrypoint import run_deposit_only
from healing_entrypoint import run_healing_only
from looting_entrypoint import run_looting_only
from targeting_entrypoint import run_targeting_only
from trade_entrypoint import run_trade_only


def _reset_diagnostics(tmp_path: Path) -> None:
    diagnostics_dir = tmp_path / 'diagnostics'
    if diagnostics_dir.exists():
        shutil.rmtree(diagnostics_dir, ignore_errors=True)


def _write_rois(tmp_path: Path, rois: dict[str, dict[str, int]]) -> str:
    p = tmp_path / 'rois.json'
    p.write_text(json.dumps({'rois': rois}), encoding='utf-8')
    return str(p)


def test_runtime_log_created_only_after_preflight_failure_cases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    # Common mock window/input ok.
    monkeypatch.setenv('FRBOT_MOCK_CAPTURE_OK', '1')
    monkeypatch.setenv('FRBOT_MOCK_INPUT_OK', '1')
    monkeypatch.setenv('FRBOT_MOCK_WINDOW_OK', '1')
    monkeypatch.setenv('FRBOT_MOCK_WINDOW_FOREGROUND', '1')
    monkeypatch.setenv('FRBOT_MOCK_WINDOW_RECT_OK', '1')

    # --- TARGETING: missing battle_list/target_frame ROIs -> preflight abort, no runtime.log
    monkeypatch.setenv('FRBOT_MODE', 'targeting')
    monkeypatch.setenv('FRBOT_TARGETING_BACKEND', 'mock')
    monkeypatch.setenv('FRBOT_ENABLE_TARGETING', '1')
    monkeypatch.setenv('FRBOT_TARGETING_MAX_TICKS', '1')
    monkeypatch.setenv('FRBOT_CONFIG_PATH', _write_rois(tmp_path, {}))
    assert run_targeting_only() == 1
    assert not (tmp_path / 'diagnostics' / 'runtime.log').exists()

    # Reset diagnostics between runs.
    _reset_diagnostics(tmp_path)

    # --- HEALING: missing hp/mp ROIs -> preflight abort
    monkeypatch.setenv('FRBOT_MODE', 'healing')
    monkeypatch.setenv('FRBOT_HEALING_BACKEND', 'mock')
    monkeypatch.setenv('FRBOT_ENABLE_HEALING', '1')
    monkeypatch.setenv('FRBOT_HEALING_MAX_TICKS', '1')
    monkeypatch.setenv('FRBOT_TICK_HZ', '100')
    monkeypatch.setenv('FRBOT_CONFIG_PATH', _write_rois(tmp_path, {'heal_cooldown': {'x': 2, 'y': 2, 'width': 6, 'height': 3}}))
    assert run_healing_only() == 1
    assert not (tmp_path / 'diagnostics' / 'runtime.log').exists()

    _reset_diagnostics(tmp_path)

    # --- COMBAT: hard-disabled -> abort, no runtime.log
    monkeypatch.setenv('FRBOT_MODE', 'combat')
    assert run_combat_only() == 1
    assert not (tmp_path / 'diagnostics' / 'runtime.log').exists()
    fatal = (tmp_path / 'diagnostics' / 'fatal.log').read_text(encoding='utf-8', errors='replace')
    assert 'feature_disabled' in fatal

    _reset_diagnostics(tmp_path)

    # --- CAVEBOT: missing minimap ROI -> preflight abort
    monkeypatch.setenv('FRBOT_MODE', 'cavebot')
    monkeypatch.setenv('FRBOT_CAVEBOT_BACKEND', 'mock')
    monkeypatch.setenv('FRBOT_CAVEBOT_MAX_TICKS', '1')
    monkeypatch.setenv('FRBOT_PLAYER_MARKER_RGB', '255,0,255')
    monkeypatch.setenv('FRBOT_PLAYER_MARKER_TOL', '5')
    monkeypatch.setenv('FRBOT_PLAYER_MARKER_MIN_PIXELS', '5')
    monkeypatch.setenv('FRBOT_PLAYER_MARKER_MAX_PIXELS', '0')
    monkeypatch.setenv('FRBOT_CAVEBOT_WAYPOINTS', '[]')
    monkeypatch.setenv('FRBOT_CONFIG_PATH', _write_rois(tmp_path, {}))
    assert run_cavebot_only() == 1
    assert not (tmp_path / 'diagnostics' / 'runtime.log').exists()

    _reset_diagnostics(tmp_path)

    # --- LOOTING: hard-disabled -> abort, no runtime.log
    monkeypatch.setenv('FRBOT_MODE', 'looting')
    assert run_looting_only() == 1
    assert not (tmp_path / 'diagnostics' / 'runtime.log').exists()
    fatal = (tmp_path / 'diagnostics' / 'fatal.log').read_text(encoding='utf-8', errors='replace')
    assert 'feature_disabled' in fatal

    _reset_diagnostics(tmp_path)

    # --- DEPOSIT: hard-disabled -> abort, no runtime.log
    monkeypatch.setenv('FRBOT_MODE', 'deposit')
    assert run_deposit_only() == 1
    assert not (tmp_path / 'diagnostics' / 'runtime.log').exists()
    fatal = (tmp_path / 'diagnostics' / 'fatal.log').read_text(encoding='utf-8', errors='replace')
    assert 'feature_disabled' in fatal

    _reset_diagnostics(tmp_path)

    # --- TRADE: hard-disabled -> abort, no runtime.log
    monkeypatch.setenv('FRBOT_MODE', 'trade')
    assert run_trade_only() == 1
    assert not (tmp_path / 'diagnostics' / 'runtime.log').exists()
    fatal = (tmp_path / 'diagnostics' / 'fatal.log').read_text(encoding='utf-8', errors='replace')
    assert 'feature_disabled' in fatal
