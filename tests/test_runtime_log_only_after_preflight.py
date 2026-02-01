from __future__ import annotations

import json
from pathlib import Path

import pytest

from cavebot_entrypoint import run_cavebot_only
from combat_entrypoint import run_combat_only
from deposit_entrypoint import run_deposit_only
from healing_entrypoint import run_healing_only
from looting_entrypoint import run_looting_only
from targeting_entrypoint import run_targeting_only
from trade_entrypoint import run_trade_only


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
    if (tmp_path / 'diagnostics').exists():
        for f in (tmp_path / 'diagnostics').glob('*'):
            f.unlink(missing_ok=True)  # type: ignore[arg-type]

    # --- HEALING: missing hp/mp ROIs -> preflight abort
    monkeypatch.setenv('FRBOT_MODE', 'healing')
    monkeypatch.setenv('FRBOT_HEALING_BACKEND', 'mock')
    monkeypatch.setenv('FRBOT_ENABLE_HEALING', '1')
    monkeypatch.setenv('FRBOT_HEALING_MAX_TICKS', '1')
    monkeypatch.setenv('FRBOT_TICK_HZ', '100')
    monkeypatch.setenv('FRBOT_CONFIG_PATH', _write_rois(tmp_path, {'heal_cooldown': {'x': 2, 'y': 2, 'width': 6, 'height': 3}}))
    assert run_healing_only() == 1
    assert not (tmp_path / 'diagnostics' / 'runtime.log').exists()

    if (tmp_path / 'diagnostics').exists():
        for f in (tmp_path / 'diagnostics').glob('*'):
            f.unlink(missing_ok=True)  # type: ignore[arg-type]

    # --- COMBAT: missing required ROIs -> preflight abort
    monkeypatch.setenv('FRBOT_MODE', 'combat')
    monkeypatch.setenv('FRBOT_COMBAT_BACKEND', 'mock')
    monkeypatch.setenv('FRBOT_ENABLE_COMBAT', '1')
    monkeypatch.setenv('FRBOT_COMBAT_MAX_TICKS', '1')
    monkeypatch.setenv('FRBOT_TICK_HZ', '100')
    monkeypatch.setenv('FRBOT_ATTACK_KEY', 'F2')
    monkeypatch.setenv('FRBOT_MOCK_BATTLE_LIST_ROWS', 'Orc:1:1')
    monkeypatch.setenv('FRBOT_MOCK_BATTLE_SELECTED_ROW', '0')
    monkeypatch.setenv('FRBOT_MOCK_HP_CURRENT', '80')
    monkeypatch.setenv('FRBOT_MOCK_HP_MAX', '100')
    monkeypatch.setenv('FRBOT_MOCK_MP_CURRENT', '80')
    monkeypatch.setenv('FRBOT_MOCK_MP_MAX', '100')
    monkeypatch.setenv('FRBOT_MOCK_TARGET_HP_CURRENT', '100')
    monkeypatch.setenv('FRBOT_MOCK_TARGET_HP_MAX', '100')
    monkeypatch.setenv('FRBOT_CONFIG_PATH', _write_rois(tmp_path, {}))
    assert run_combat_only() == 1
    assert not (tmp_path / 'diagnostics' / 'runtime.log').exists()

    if (tmp_path / 'diagnostics').exists():
        for f in (tmp_path / 'diagnostics').glob('*'):
            f.unlink(missing_ok=True)  # type: ignore[arg-type]

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

    if (tmp_path / 'diagnostics').exists():
        for f in (tmp_path / 'diagnostics').glob('*'):
            f.unlink(missing_ok=True)  # type: ignore[arg-type]

    # --- LOOTING: missing inventory_text ROI -> preflight abort
    monkeypatch.setenv('FRBOT_MODE', 'looting')
    monkeypatch.setenv('FRBOT_LOOTING_BACKEND', 'mock')
    monkeypatch.setenv('FRBOT_TICK_HZ', '100')
    monkeypatch.setenv('FRBOT_LOOTING_MAX_TICKS', '1')
    monkeypatch.setenv('FRBOT_LOOTING_MAX_ATTEMPTS', '1')
    monkeypatch.setenv('FRBOT_QUICK_LOOT_KEY', 'R')
    monkeypatch.setenv('FRBOT_LOOTING_MODE', 'premium')
    monkeypatch.setenv('MOCK_LOOT_PREMIUM', 'true')
    monkeypatch.setenv('FRBOT_CONFIG_PATH', _write_rois(tmp_path, {}))
    assert run_looting_only() == 1
    assert not (tmp_path / 'diagnostics' / 'runtime.log').exists()

    if (tmp_path / 'diagnostics').exists():
        for f in (tmp_path / 'diagnostics').glob('*'):
            f.unlink(missing_ok=True)  # type: ignore[arg-type]

    # --- DEPOSIT: missing inventory_text/depot_container -> preflight abort
    monkeypatch.setenv('FRBOT_MODE', 'deposit')
    monkeypatch.setenv('FRBOT_DEPOSIT_BACKEND', 'mock')
    monkeypatch.setenv('FRBOT_TICK_HZ', '100')
    monkeypatch.setenv('FRBOT_DEPOSIT_KEY', 'D')
    monkeypatch.setenv('FRBOT_DEPOSIT_MAX_TICKS', '1')
    monkeypatch.setenv('FRBOT_DEPOSIT_MAX_ATTEMPTS', '1')
    monkeypatch.setenv('FRBOT_INVENTORY_TEXT_ROI', 'inventory_text')
    monkeypatch.setenv('FRBOT_DEPOT_CONTAINER_ROI', 'depot_container')
    monkeypatch.setenv('FRBOT_MOCK_INV_GOLD', '5')
    monkeypatch.setenv('FRBOT_MOCK_INV_CAP_USED', '5')
    monkeypatch.setenv('FRBOT_MOCK_DEPOT_COUNT', '0')
    monkeypatch.setenv('FRBOT_CONFIG_PATH', _write_rois(tmp_path, {}))
    assert run_deposit_only() == 1
    assert not (tmp_path / 'diagnostics' / 'runtime.log').exists()

    if (tmp_path / 'diagnostics').exists():
        for f in (tmp_path / 'diagnostics').glob('*'):
            f.unlink(missing_ok=True)  # type: ignore[arg-type]

    # --- TRADE: missing trade ROIs -> preflight abort
    monkeypatch.setenv('FRBOT_MODE', 'trade')
    monkeypatch.setenv('FRBOT_TRADE_BACKEND', 'mock')
    monkeypatch.setenv('FRBOT_TICK_HZ', '100')
    monkeypatch.setenv('FRBOT_TRADE_ACTION', 'buy')
    monkeypatch.setenv('FRBOT_TRADE_MAX_TICKS', '1')
    monkeypatch.setenv('FRBOT_TRADE_MAX_ATTEMPTS', '1')
    monkeypatch.setenv('FRBOT_TRADE_EXPECTED_NPC_ID', '7')
    monkeypatch.setenv('FRBOT_TRADE_INVENTORY_ROI', 'trade_inventory')
    monkeypatch.setenv('FRBOT_TRADE_NPC_ROI', 'trade_npc')
    monkeypatch.setenv('FRBOT_TRADE_ACTION_ROI', 'trade_action')
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
    monkeypatch.setenv('FRBOT_CONFIG_PATH', _write_rois(tmp_path, {}))
    assert run_trade_only() == 1
    assert not (tmp_path / 'diagnostics' / 'runtime.log').exists()
